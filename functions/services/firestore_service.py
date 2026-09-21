"""Firestore service for TrueCost.

Provides CRUD operations for estimates and agent outputs.
"""

from config.safe_logging import safe_error_text

from typing import Dict, Any, Optional, List
from datetime import datetime
import inspect
import time
import structlog
from google.api_core.exceptions import FailedPrecondition, Conflict, Aborted
from services.cost_execution import bounded_storage_call

from firebase_admin import firestore

from config.errors import TrueCostError, ErrorCode

logger = structlog.get_logger()


class FirestoreService:
    """Service for Firestore operations.
    
    Handles all database operations for estimates, agent outputs,
    and pipeline status updates.
    
    Note: Firebase Admin SDK for Python is synchronous. Methods are
    marked async for interface compatibility but operations are sync.
    """
    
    COLLECTION_ESTIMATES = "estimates"
    COLLECTION_PROJECTS = "projects"
    SUBCOLLECTION_PIPELINE = "pipeline"
    SUBCOLLECTION_AGENT_OUTPUTS = "agentOutputs"
    SUBCOLLECTION_CONVERSATIONS = "conversations"
    SUBCOLLECTION_VERSIONS = "versions"
    SUBCOLLECTION_COST_ITEMS = "costItems"

    # Map Python agent names to frontend stage names
    AGENT_TO_STAGE_MAP = {
        "location": "location",
        "scope": "scope",
        "code_compliance": "code_compliance",
        "cost": "cost",
        "risk": "risk",
        "timeline": "timeline",
        "final": "final",
    }
    
    def __init__(self, db=None, durable_context=None):
        """Initialize FirestoreService.
        
        Args:
            db: Optional Firestore client. If not provided, uses default.
        """
        self._db = db
        self._durable = durable_context
    
    async def _ensure_legacy_write(self, estimate_id):
        """A durable estimate must never fall back to unbound legacy writes."""
        from services.durable_execution import Rejected
        if self._durable is not None:
            raise Rejected('Unrouted durable write')
        ref = self.db.collection(self.COLLECTION_ESTIMATES).document(estimate_id)
        snapshot = await self._maybe_await(ref.get(retry=None, timeout=3))
        data = snapshot.to_dict() if snapshot.exists else None
        if isinstance(data, dict) and data.get('durableJobId'):
            raise Rejected('Durable execution context required')

    @property
    def db(self):
        """Get Firestore client (lazy initialization)."""
        if self._db is None:
            self._db = firestore.client()
        return self._db

    async def _maybe_await(self, result: Any) -> Any:
        """Await result if it is awaitable (supports AsyncMock in unit tests)."""
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _cost_terminal(data):
        terminal = {"failed", "completed", "complete", "error", "cancelled"}
        return (data.get("status") in terminal or
                data.get("pipelineStatus", {}).get("status") in terminal)

    async def _mutate_cost(self, estimate_id, change):
        """Optimistic atomic write: parent version fences output and item writes.

        A batch precondition avoids an unbounded synchronous transaction. Each
        RPC has a 3s timeout and no SDK retries; conflicts retry at most 3 times.
        Cancellation joins in-flight IO before the request loop can close.
        """
        await self._ensure_legacy_write(estimate_id)
        ref = self.db.collection(self.COLLECTION_ESTIMATES).document(estimate_id)
        for _ in range(3):
            snapshot = await bounded_storage_call(lambda: ref.get(retry=None, timeout=3.0))
            if not snapshot.exists:
                raise TrueCostError(ErrorCode.ESTIMATE_NOT_FOUND, "Cost estimate not found")
            mutation = change(snapshot.to_dict())
            if mutation is None:
                return
            updates, writes = mutation
            batch = self.db.batch()
            batch.update(ref, {**updates, "updatedAt": firestore.SERVER_TIMESTAMP},
                         option=self.db.write_option(last_update_time=snapshot.update_time))
            for doc_ref, data, merge in writes:
                batch.set(doc_ref, data, merge=merge)
            try:
                await bounded_storage_call(lambda: batch.commit(retry=None, timeout=3.0))
                return
            except (FailedPrecondition, Conflict, Aborted):
                continue
        raise TrueCostError(ErrorCode.FIRESTORE_WRITE_FAILED, "Cost write conflicted with newer state")

    async def begin_cost_attempt(self, estimate_id, attempt_id, expires_at):
        def change(data):
            if self._cost_terminal(data):
                raise TrueCostError(ErrorCode.AGENT_FAILED, "Cannot start Cost on a terminal pipeline")
            return {"costAttempt": {"id": attempt_id, "active": True,
                                    "expiresAt": expires_at}}, []
        await self._mutate_cost(estimate_id, change)

    async def end_cost_attempt(self, estimate_id, attempt_id):
        def change(data):
            if data.get("costAttempt", {}).get("id") != attempt_id:
                return None
            return {"costAttempt.active": False}, []
        await self._mutate_cost(estimate_id, change)

    async def _write_cost_attempt(self, estimate_id, attempt_id, updates, writes=(), *, allow_inactive=False):
        def change(data):
            attempt = data.get("costAttempt")
            valid = attempt is None and attempt_id is None  # Direct/offline callers
            if attempt and attempt_id == attempt.get("id"):
                valid = allow_inactive or (attempt.get("active") and
                                          time.time() < attempt.get("expiresAt", 0))
            if not valid or self._cost_terminal(data):
                raise TrueCostError(ErrorCode.AGENT_FAILED, "Stale or abandoned Cost attempt")
            return updates, writes
        await self._mutate_cost(estimate_id, change)
    
    async def get_estimate(self, estimate_id: str) -> Optional[Dict[str, Any]]:
        """Fetch estimate document by ID.
        
        Args:
            estimate_id: The estimate document ID.
            
        Returns:
            Estimate document data or None if not found.
            
        Raises:
            TrueCostError: If Firestore operation fails.
        """
        try:
            doc_ref = self.db.collection(self.COLLECTION_ESTIMATES).document(estimate_id)
            doc = await self._maybe_await(doc_ref.get())
            
            if doc.exists:
                return {"id": doc.id, **doc.to_dict()}
            return None
            
        except Exception as e:
            logger.error("firestore_get_failed", estimate_id=estimate_id, error=safe_error_text(e))
            raise TrueCostError(
                code=ErrorCode.FIRESTORE_ERROR,
                message=f"Failed to get estimate: {safe_error_text(e)}",
                details={"estimate_id": estimate_id}
            )
    
    async def update_estimate(
        self,
        estimate_id: str,
        data: Dict[str, Any]
    ) -> None:
        """Update estimate document.
        
        Args:
            estimate_id: The estimate document ID.
            data: Fields to update (supports dot notation for nested fields).
            
        Raises:
            TrueCostError: If Firestore operation fails.
        """
        if self._durable is not None:
            return await self._durable.write(estimate_id, 'rootPatch', data)
        await self._ensure_legacy_write(estimate_id)

        try:
            doc_ref = self.db.collection(self.COLLECTION_ESTIMATES).document(estimate_id)
            
            # Add timestamp
            data["updatedAt"] = firestore.SERVER_TIMESTAMP
            
            await self._maybe_await(doc_ref.update(data))
            logger.info("estimate_updated", estimate_id=estimate_id, fields=list(data.keys()))
            
        except Exception as e:
            logger.error("firestore_update_failed", estimate_id=estimate_id, error=safe_error_text(e))
            raise TrueCostError(
                code=ErrorCode.FIRESTORE_WRITE_FAILED,
                message=f"Failed to update estimate: {safe_error_text(e)}",
                details={"estimate_id": estimate_id}
            )
    
    async def update_agent_status(
        self,
        estimate_id: str,
        agent_name: str,
        status: str,
        retry: Optional[int] = None,
        attempt_id: Optional[str] = None,
    ) -> None:
        """Update pipeline status for an agent.
        
        Args:
            estimate_id: The estimate document ID.
            agent_name: Name of the agent.
            status: Status string (pending, running, completed, failed).
            retry: Optional retry attempt number.
        """
        if self._durable is not None:
            return await self._durable.write(estimate_id, 'agentStatus',
                {'status': status, 'retry': retry}, agent_name=agent_name, attempt_id=attempt_id)
        await self._ensure_legacy_write(estimate_id)

        update_data = {
            f"pipelineStatus.agentStatuses.{agent_name}": status,
            "pipelineStatus.currentAgent": agent_name,
            "pipelineStatus.lastUpdated": firestore.SERVER_TIMESTAMP
        }
        
        if retry is not None:
            update_data[f"pipelineStatus.retries.{agent_name}"] = retry
        
        if agent_name == "cost":
            await self._write_cost_attempt(estimate_id, attempt_id, update_data)
        else:
            await self.update_estimate(estimate_id, update_data)
        logger.info("agent_status_updated", estimate_id=estimate_id, agent=agent_name, status=status)

    async def sync_to_project_pipeline(
        self,
        project_id: str,
        estimate_id: str,
        current_agent: Optional[str],
        completed_agents: List[str],
        progress: int,
        status: str = "running",
        error: Optional[str] = None,
        user_id: Optional[str] = None,
        started_at: Optional[int] = None,
    ) -> None:
        """Sync pipeline status to the project collection for frontend UI.

        The frontend UI watches /projects/{projectId}/pipeline/status for
        real-time progress updates. This method syncs the Python pipeline
        status to that location.

        Args:
            project_id: The project document ID.
            estimate_id: The estimate document ID (used as pipelineId).
            current_agent: Current agent name (will be mapped to stage name).
            completed_agents: List of completed agent names.
            progress: Progress percentage (0-100).
            status: Pipeline status ('running', 'complete', 'error', 'idle').
            error: Optional error message.
            user_id: User who triggered the pipeline.
            started_at: Pipeline start timestamp (ms since epoch).
        """
        await self._ensure_legacy_write(estimate_id)

        if not project_id:
            logger.warning("sync_skipped_no_project_id", estimate_id=estimate_id)
            return

        try:
            # Map agent names to stage names for the frontend
            current_stage = self.AGENT_TO_STAGE_MAP.get(current_agent) if current_agent else None
            completed_stages = [
                self.AGENT_TO_STAGE_MAP.get(agent, agent)
                for agent in completed_agents
                if agent in self.AGENT_TO_STAGE_MAP
            ]
            # Remove duplicates while preserving order
            completed_stages = list(dict.fromkeys(completed_stages))

            # Build status document matching frontend expectations
            status_data = {
                "status": status,
                "currentStage": current_stage,
                "completedStages": completed_stages,
                "progress": progress,
                "pipelineId": estimate_id,
                "projectId": project_id,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }

            if started_at:
                status_data["startedAt"] = started_at
            if user_id:
                status_data["triggeredBy"] = user_id
            if error:
                status_data["error"] = error
            if status == "complete":
                import time
                status_data["completedAt"] = int(time.time() * 1000)

            # Write to /projects/{projectId}/pipeline/status
            doc_ref = (
                self.db
                .collection(self.COLLECTION_PROJECTS)
                .document(project_id)
                .collection(self.SUBCOLLECTION_PIPELINE)
                .document("status")
            )

            await self._maybe_await(doc_ref.set(status_data, merge=True))

            logger.info(
                "project_pipeline_synced",
                project_id=project_id,
                estimate_id=estimate_id,
                status=status,
                progress=progress,
                current_stage=current_stage,
            )

        except Exception as e:
            # Don't fail the pipeline if sync fails - just log warning
            logger.warning(
                "project_pipeline_sync_failed",
                project_id=project_id,
                estimate_id=estimate_id,
                error=safe_error_text(e),
            )

    async def save_agent_output(
        self,
        estimate_id: str,
        agent_name: str,
        output: Dict[str, Any],
        summary: Optional[str] = None,
        confidence: Optional[float] = None,
        tokens_used: Optional[int] = None,
        duration_ms: Optional[int] = None,
        score: Optional[int] = None,
        attempt_id: Optional[str] = None,
        allow_inactive: bool = False,
    ) -> None:
        """Save agent output to subcollection.
        
        Args:
            estimate_id: The estimate document ID.
            agent_name: Name of the agent.
            output: Agent output data.
            summary: Human-readable summary.
            confidence: Confidence score (0-1).
            tokens_used: Number of LLM tokens used.
            duration_ms: Processing duration in milliseconds.
            score: Scorer agent score (0-100).
            
        Raises:
            TrueCostError: If Firestore operation fails.
        """
        if self._durable is not None:
            return await self._durable.write(estimate_id, 'output', output,
                agent_name=agent_name, attempt_id=attempt_id)
        await self._ensure_legacy_write(estimate_id)

        try:
            doc_ref = (
                self.db
                .collection(self.COLLECTION_ESTIMATES)
                .document(estimate_id)
                .collection(self.SUBCOLLECTION_AGENT_OUTPUTS)
                .document(agent_name)
            )
            
            agent_output_data = {
                "status": "completed",
                "output": output,
                "summary": summary,
                "confidence": confidence,
                "tokensUsed": tokens_used,
                "durationMs": duration_ms,
                "score": score,
                "createdAt": firestore.SERVER_TIMESTAMP,
                "updatedAt": firestore.SERVER_TIMESTAMP
            }
            
            # Remove None values
            agent_output_data = {k: v for k, v in agent_output_data.items() if v is not None}

            if agent_name == "cost":
                await self._write_cost_attempt(
                    estimate_id, attempt_id,
                    {"costOutput": output, "pipelineStatus.agentStatuses.cost": "completed"},
                    [(doc_ref, agent_output_data, False)],
                    allow_inactive=allow_inactive,
                )
                return
            
            await self._maybe_await(doc_ref.set(agent_output_data))
            
            # Also update the main estimate document with agent output
            await self.update_estimate(estimate_id, {
                f"{agent_name}Output": output,
                f"pipelineStatus.agentStatuses.{agent_name}": "completed"
            })
            
            logger.info(
                "agent_output_saved",
                estimate_id=estimate_id,
                agent=agent_name,
                tokens=tokens_used,
                duration_ms=duration_ms
            )
            
        except Exception as e:
            logger.error(
                "agent_output_save_failed",
                estimate_id=estimate_id,
                agent=agent_name,
                error=safe_error_text(e)
            )
            raise TrueCostError(
                code=ErrorCode.FIRESTORE_WRITE_FAILED,
                message=f"Failed to save agent output: {safe_error_text(e)}",
                details={"estimate_id": estimate_id, "agent_name": agent_name}
            )

    async def save_cost_items(
        self,
        estimate_id: str,
        items: List[Dict[str, Any]],
        attempt_id: Optional[str] = None,
    ) -> int:
        """Save granular cost items to a dedicated subcollection.

        This is used to persist high-granularity BOM/material takeoff data
        without risking the Firestore 1MB document size limit on the root
        estimate document.

        Data is stored at:
          /estimates/{estimateId}/costItems/{costItemId}

        Args:
            estimate_id: The estimate document ID.
            items: List of cost item dicts. If an item has an "id" field it will
                be used as the document ID; otherwise Firestore will generate one.

        Returns:
            Number of items written.
        """
        if self._durable is not None:
            await self._durable.write(estimate_id, 'costItems', items, attempt_id=attempt_id)
            return sum(isinstance(item, dict) for item in items)
        await self._ensure_legacy_write(estimate_id)

        if not items:
            return 0

        try:
            coll_ref = (
                self.db
                .collection(self.COLLECTION_ESTIMATES)
                .document(estimate_id)
                .collection(self.SUBCOLLECTION_COST_ITEMS)
            )

            writes = []
            written = 0

            for item in items:
                if not isinstance(item, dict):
                    continue

                item_id = item.get("id")
                doc_ref = coll_ref.document(item_id) if item_id else coll_ref.document()

                data = {**item}
                data["updatedAt"] = firestore.SERVER_TIMESTAMP
                if "createdAt" not in data:
                    data["createdAt"] = firestore.SERVER_TIMESTAMP

                writes.append((doc_ref, data, True))
                written += 1

            if written:
                await self._write_cost_attempt(estimate_id, attempt_id, {}, writes)

            return written
        except Exception as e:
            logger.error("cost_items_save_failed", estimate_id=estimate_id, error=safe_error_text(e))
            raise TrueCostError(
                code=ErrorCode.FIRESTORE_WRITE_FAILED,
                message=f"Failed to save cost items: {safe_error_text(e)}",
                details={"estimate_id": estimate_id}
            )

    async def list_cost_items(
        self,
        estimate_id: str,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List granular cost items for an estimate.

        Args:
            estimate_id: The estimate document ID.
            limit: Optional maximum number of items to return.

        Returns:
            List of cost item documents (each includes "id").
        """
        if self._durable is not None:
            return await self._durable.list_cost_items(estimate_id, limit)

        try:
            coll_ref = (
                self.db
                .collection(self.COLLECTION_ESTIMATES)
                .document(estimate_id)
                .collection(self.SUBCOLLECTION_COST_ITEMS)
            )

            query = coll_ref
            if limit is not None:
                query = query.limit(int(limit))

            docs = query.stream()
            results: List[Dict[str, Any]] = []
            for doc in docs:
                results.append({"id": doc.id, **(doc.to_dict() or {})})
            return results
        except Exception as e:
            logger.error("cost_items_list_failed", estimate_id=estimate_id, error=safe_error_text(e))
            return []
    
    async def get_agent_output(
        self,
        estimate_id: str,
        agent_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get agent output from subcollection.
        
        Args:
            estimate_id: The estimate document ID.
            agent_name: Name of the agent.
            
        Returns:
            Agent output data or None if not found.
        """
        try:
            doc_ref = (
                self.db
                .collection(self.COLLECTION_ESTIMATES)
                .document(estimate_id)
                .collection(self.SUBCOLLECTION_AGENT_OUTPUTS)
                .document(agent_name)
            )
            
            doc = await self._maybe_await(doc_ref.get())
            if doc.exists:
                return doc.to_dict()
            return None
            
        except Exception as e:
            logger.error(
                "agent_output_get_failed",
                estimate_id=estimate_id,
                agent=agent_name,
                error=safe_error_text(e)
            )
            return None
    
    async def delete_estimate(self, estimate_id: str) -> None:
        """Delete estimate and all subcollections.
        
        Args:
            estimate_id: The estimate document ID.
            
        Raises:
            TrueCostError: If Firestore operation fails.
        """
        await self._ensure_legacy_write(estimate_id)

        try:
            estimate_ref = self.db.collection(self.COLLECTION_ESTIMATES).document(estimate_id)
            
            # Delete subcollections
            subcollections = [
                self.SUBCOLLECTION_AGENT_OUTPUTS,
                self.SUBCOLLECTION_COST_ITEMS,
                self.SUBCOLLECTION_CONVERSATIONS,
                self.SUBCOLLECTION_VERSIONS
            ]
            
            for subcollection_name in subcollections:
                subcollection = estimate_ref.collection(subcollection_name)
                docs = subcollection.stream()  # Synchronous generator
                for doc in docs:
                    await self._maybe_await(doc.reference.delete())
            
            # Delete main document
            await self._maybe_await(estimate_ref.delete())
            
            logger.info("estimate_deleted", estimate_id=estimate_id)
            
        except Exception as e:
            logger.error("estimate_delete_failed", estimate_id=estimate_id, error=safe_error_text(e))
            raise TrueCostError(
                code=ErrorCode.FIRESTORE_ERROR,
                message=f"Failed to delete estimate: {safe_error_text(e)}",
                details={"estimate_id": estimate_id}
            )
    
    async def create_estimate(
        self,
        estimate_id: str,
        user_id: str,
        clarification_output: Dict[str, Any],
        create_only: bool = False
    ) -> str:
        """Create a new estimate document.
        
        Args:
            estimate_id: The estimate document ID.
            user_id: The user's ID.
            clarification_output: ClarificationOutput from Dev 3.
            
        Returns:
            The created estimate ID.
        """
        await self._ensure_legacy_write(estimate_id)

        try:
            doc_ref = self.db.collection(self.COLLECTION_ESTIMATES).document(estimate_id)
            
            estimate_data = {
                "userId": user_id,
                "status": "processing",
                "clarificationOutput": clarification_output,
                "pipelineStatus": {
                    "currentAgent": None,
                    "completedAgents": [],
                    "progress": 0,
                    "agentStatuses": {},
                    "scores": {},
                    "retries": {}
                },
                "createdAt": firestore.SERVER_TIMESTAMP,
                "updatedAt": firestore.SERVER_TIMESTAMP
            }
            
            await self._maybe_await(doc_ref.create(estimate_data) if create_only else doc_ref.set(estimate_data))
            logger.info("estimate_created", estimate_id=estimate_id, user_id=user_id)
            
            return estimate_id
            
        except Exception as e:
            logger.error("estimate_create_failed", estimate_id=estimate_id, error=safe_error_text(e))
            raise TrueCostError(
                code=ErrorCode.FIRESTORE_WRITE_FAILED,
                message=f"Failed to create estimate: {safe_error_text(e)}",
                details={"estimate_id": estimate_id}
            )
