import {createHash} from 'node:crypto';
import {safeLog, safeErrorMessage} from './safeDiagnostics';
import { isProduction, productionPythonUrl } from './productionConfig';
/**
 * Estimate Pipeline Orchestrator
 * Dedicated cloud function to trigger and initialize the estimate generation pipeline.
 * Story: 6-2 - Two-phase UI with progress tracking
 */

import { HttpsError } from 'firebase-functions/v2/https';
import { onCall, allowedOrigins } from './functionSecurity';
import { getFirestore, FieldValue, Firestore } from 'firebase-admin/firestore';
import { initializeApp, getApps } from 'firebase-admin/app';

// Lazy initialization to avoid timeout during module load
let _db: Firestore | null = null;

function getDb(): Firestore {
  if (!_db) {
    // Initialize Firebase Admin if not already initialized
    if (getApps().length === 0) {
      initializeApp();
    }
    _db = getFirestore();
  }
  return _db;
}

/**
 * Project context data gathered for the pipeline
 */
interface ProjectContext {
  projectId: string;
  projectName: string;
  projectDescription: string;
  backgroundImage: {
    url: string;
    width: number;
    height: number;
  } | null;
  scopeItems: Array<{
    scope: string;
    description: string;
  }>;
  shapes: Array<{
    id: string;
    type: string;
    x: number;
    y: number;
    w: number;
    h: number;
    // Additional shape properties
    [key: string]: unknown;
  }>;
}

/**
 * Pipeline stages for the estimate generation pipeline
 * Note: Clarification runs separately during Annotate phase (Epic 3)
 */
const _PIPELINE_STAGES = [
  'cad_analysis',
  'location',
  'scope',
  'code_compliance',
  'cost',
  'risk',
  'timeline',
  'final',
] as const;

type PipelineStageId = typeof _PIPELINE_STAGES[number];

interface PipelineStatus {
  status: 'idle' | 'running' | 'complete' | 'error';
  currentStage: PipelineStageId | null;
  completedStages: PipelineStageId[];
  startedAt: number | null;
  completedAt: number | null;
  error?: string;
  triggeredBy: string;
  projectId: string;
}

/**
 * Get the Python pipeline URL based on environment
 */
export function getPythonPipelineUrl(): string {
  if (isProduction()) return `${productionPythonUrl()}/start_deep_pipeline`;
  // Allow override via environment variable for flexible local dev
  if (process.env.PYTHON_FUNCTIONS_URL) {
    const base = process.env.PYTHON_FUNCTIONS_URL.replace(/\/+$/, '').replace(/\/start_deep_pipeline$/, '');
    const url = new URL(base);
    const prefix = url.pathname === '/' && ['localhost', '127.0.0.1'].includes(url.hostname)
      ? `${base}/collabcanvas-dev/us-central1` : base;
    return `${prefix}/start_deep_pipeline`;
  }
  // Check if we're running in the emulator
  if (process.env.FUNCTIONS_EMULATOR === 'true') {
    // Python functions run on separate port (5003) to avoid conflict with TS emulator (5001)
    // Start Python server: cd ../functions && source venv/bin/activate && python serve_local.py
    return 'http://127.0.0.1:5003/collabcanvas-dev/us-central1/start_deep_pipeline';
  }
  // Production URL
  return 'https://us-central1-collabcanvas-dev.cloudfunctions.net/start_deep_pipeline';
}

/**
 * Gather project context data for the pipeline
 */
async function gatherProjectContext(projectId: string): Promise<ProjectContext> {
  const db = getDb();

  // Get project document
  const projectRef = db.collection('projects').doc(projectId);
  const projectDoc = await projectRef.get();
  const projectData = projectDoc.data();

  // Get board state (background image)
  const boardRef = db.collection('projects').doc(projectId).collection('board').doc('state');
  const boardDoc = await boardRef.get();
  const boardData = boardDoc.exists ? boardDoc.data() : null;

  // Get scope items
  const scopeRef = db.collection('projects').doc(projectId).collection('scope').doc('data');
  const scopeDoc = await scopeRef.get();
  const scopeData = scopeDoc.exists ? scopeDoc.data() : null;

  // Get shapes (annotations)
  const shapesRef = db.collection('projects').doc(projectId).collection('shapes');
  const shapesSnapshot = await shapesRef.get();
  const shapes: ProjectContext['shapes'] = [];
  shapesSnapshot.forEach((doc) => {
    const data = doc.data();
    shapes.push({
      id: doc.id,
      type: data.type,
      x: data.x,
      y: data.y,
      w: data.w,
      h: data.h,
      ...data,
    });
  });

  return {
    projectId,
    projectName: projectData?.name || '',
    projectDescription: projectData?.description || '',
    backgroundImage: boardData?.backgroundImage
      ? {
          url: boardData.backgroundImage.url,
          width: boardData.backgroundImage.width,
          height: boardData.backgroundImage.height,
        }
      : null,
    scopeItems: scopeData?.items || [],
    shapes,
  };
}

/**
 * Trigger the estimate generation pipeline
 * This function:
 * 1. Validates the request
 * 2. Gathers project context (scope, plan, annotations)
 * 3. Creates/updates the pipeline status document with context
 * 4. Returns a pipelineId for tracking
 *
 * The actual agent execution is handled by the existing deep pipeline
 * infrastructure from Epic 2.
 */
export const triggerEstimatePipeline = onCall({
  cors: allowedOrigins(),
  maxInstances: 10,
  memory: '512MiB', // Increased for context gathering
}, async (request) => {
  try {
    const { projectId, clarificationOutput: suppliedClarification } = request.data;

    // Validate required fields
    if (!projectId) {
      throw new HttpsError('invalid-argument', 'Project ID is required');
    }

    // =================== SECURITY: Authentication Check ===================
    // Verify auth first and bind userId from the authenticated identity
    // IMPORTANT: Never trust request.data.userId - always use request.auth.uid
    if (!request.auth) {
      throw new HttpsError('unauthenticated', 'User must be authenticated');
    }

    // Bind userId from authenticated identity - ignore any caller-supplied userId
    const userId = request.auth.uid;
    if (isProduction()) {
      if (!suppliedClarification || typeof suppliedClarification !== 'object') {
        throw new HttpsError('invalid-argument', 'Completed clarification required');
      }
      const canonical=(value:any):any=> Array.isArray(value)?value.map(canonical):
        value && typeof value==='object'?Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>[k,canonical(v)])):value;
      const key=request.data.idempotencyKey || suppliedClarification.estimateId ||
        'est-'+createHash('sha256').update(JSON.stringify(canonical({projectId,clarification:suppliedClarification}))).digest('hex');
      const authorization=request.rawRequest.headers.authorization;
      if(!authorization || !/^Bearer \S+$/i.test(authorization)) throw new HttpsError('unauthenticated','Caller ID token required');
      const response=await fetch(getPythonPipelineUrl(),{method:'POST',redirect:'error',
        signal:AbortSignal.timeout(15000),headers:{'Content-Type':'application/json',Authorization:authorization},
        body:JSON.stringify({userId,projectId,idempotencyKey:key,clarificationOutput:suppliedClarification})});
      const result=await response.json();
      if(response.status!==202 || !result.success || !result.data?.jobId) throw new HttpsError('unavailable','Durable start unavailable');
      return {...result.data,success:true,pipelineId:result.data.estimateId};
    }


    const db = getDb();

    // Verify the user has access to this project
    const projectRef = db.collection('projects').doc(projectId);
    const projectDoc = await projectRef.get();

    if (!projectDoc.exists) {
      throw new HttpsError('not-found', 'Project not found');
    }

    const projectData = projectDoc.data();
    if (projectData?.ownerId !== request.auth.uid) {
      // Check if user is a collaborator
      const collaborators = projectData?.collaborators || [];
      const isCollaborator = collaborators.some(
        (c: { userId: string; role: string }) => c.userId === request.auth?.uid && c.role === 'editor'
      );
      if (!isCollaborator) {
        throw new HttpsError('permission-denied', 'User does not have access to this project');
      }
    }

    // Gather project context data
    safeLog('estimatePipelineOrchestrator.log', `[PIPELINE] Gathering context for project ${projectId}`);
    const projectContext = await gatherProjectContext(projectId);
    safeLog('estimatePipelineOrchestrator.log', `[PIPELINE] Context gathered:`, {
      projectName: projectContext.projectName,
      hasBackgroundImage: !!projectContext.backgroundImage,
      scopeItemCount: projectContext.scopeItems.length,
      shapeCount: projectContext.shapes.length,
    });

    // Generate pipeline ID
    const pipelineId = `pipeline_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    const startedAt = Date.now();

    if (!suppliedClarification || typeof suppliedClarification !== 'object' ||
        !suppliedClarification.projectBrief || typeof suppliedClarification.projectBrief !== 'object') {
      throw new HttpsError('invalid-argument', 'A completed clarificationOutput is required');
    }
    const clarificationOutput = { ...suppliedClarification, estimateId: pipelineId };

    // Initialize pipeline status document with context
    // Start from cad_analysis (clarification runs separately in Annotate phase)
    const pipelineStatus: PipelineStatus = {
      status: 'running',
      currentStage: 'cad_analysis',
      completedStages: [],
      startedAt,
      completedAt: null,
      triggeredBy: userId,
      projectId,
    };

    // Create/update the pipeline status document
    const statusRef = db.collection('projects').doc(projectId).collection('pipeline').doc('status');
    await statusRef.set({
      ...pipelineStatus,
      pipelineId,
      updatedAt: FieldValue.serverTimestamp(),
    });

    // Store the project context separately for agent consumption
    const contextRef = db.collection('projects').doc(projectId).collection('pipeline').doc('context');
    await contextRef.set({
      ...projectContext,
      pipelineId,
      createdAt: FieldValue.serverTimestamp(),
    });

    safeLog('estimatePipelineOrchestrator.log', `[PIPELINE] Started pipeline ${pipelineId} for project ${projectId}`);

    // Trigger the Python deep agent pipeline
    // The Python pipeline will sync progress back to /projects/{projectId}/pipeline/status
    try {
      const pythonPipelineUrl = getPythonPipelineUrl();
      safeLog('estimatePipelineOrchestrator.log', `[PIPELINE] Calling Python pipeline at: ${pythonPipelineUrl}`);

      // Forward completed clarification; never fabricate agent inputs.

      // Firebase onCall already verified this caller; Python independently verifies the same token.
      const authorization = request.rawRequest.headers.authorization;
      if (!authorization || !/^Bearer \S+$/i.test(authorization)) {
        throw new HttpsError('unauthenticated', 'Caller ID token required');
      }
      const response = await fetch(pythonPipelineUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: authorization,
        },
        body: JSON.stringify({
          userId,
          projectId, // Pass projectId for UI sync
          clarificationOutput,
        }),
      });

      const pythonResult = await response.json();

      if (!response.ok || !pythonResult.success) {
        safeLog('estimatePipelineOrchestrator.error', '[PIPELINE] Python pipeline failed', {status: response.status});
        // Update status to error but don't throw - let the user see partial progress
        await statusRef.update({
          status: 'error',
          error: safeErrorMessage(pythonResult.error?.message || 'Python pipeline failed to start'),
          updatedAt: FieldValue.serverTimestamp(),
        });
        throw new HttpsError('internal', 'Python pipeline failed to start');
      } else {
        safeLog('estimatePipelineOrchestrator.log', '[PIPELINE] Python pipeline started:', pythonResult);
      }
    } catch (pythonError) {
      safeLog('estimatePipelineOrchestrator.error', '[PIPELINE] Failed to call Python pipeline');
      await statusRef.update({ status: 'error', error: 'Python pipeline failed to start', updatedAt: FieldValue.serverTimestamp() });
      throw new HttpsError('internal', 'Python pipeline failed to start');
    }

    return {
      success: true,
      pipelineId,
      message: 'Pipeline started successfully',
      status: pipelineStatus,
      context: {
        projectName: projectContext.projectName,
        hasBackgroundImage: !!projectContext.backgroundImage,
        scopeItemCount: projectContext.scopeItems.length,
        shapeCount: projectContext.shapes.length,
      },
    };

  } catch (error) {
    safeLog('estimatePipelineOrchestrator.error', '[PIPELINE] Error starting pipeline');

    if (error instanceof HttpsError) {
      throw error;
    }

    throw new HttpsError(
      'internal',
      'Operation failed'
    );
  }
});

/**
 * Update pipeline stage (called by agent functions as they complete)
 */
// Valid pipeline stage values for validation
const VALID_PIPELINE_STAGES: readonly string[] = _PIPELINE_STAGES;

/**
 * Validate that a stage value is a valid pipeline stage
 */
function isValidPipelineStage(stage: unknown): stage is PipelineStageId {
  return typeof stage === 'string' && VALID_PIPELINE_STAGES.includes(stage);
}

export const updatePipelineStage = onCall({
  cors: allowedOrigins(),
  maxInstances: 20,
  memory: '256MiB',
}, async (request) => {
  try {
    const { projectId, completedStage, nextStage, error } = request.data;
    if(isProduction()) throw new HttpsError('failed-precondition','Durable worker owns production progress');

    if (!projectId) {
      throw new HttpsError('invalid-argument', 'Project ID is required');
    }

    // =================== SECURITY: Authentication Check ===================
    if (!request.auth) {
      throw new HttpsError('unauthenticated', 'User must be authenticated');
    }

    // =================== SECURITY: Enum Validation ===================
    // Validate completedStage if provided
    if (completedStage !== undefined && completedStage !== null && !isValidPipelineStage(completedStage)) {
      throw new HttpsError(
        'invalid-argument',
        'Operation failed'
      );
    }

    // Validate nextStage if provided
    if (nextStage !== undefined && nextStage !== null && !isValidPipelineStage(nextStage)) {
      throw new HttpsError(
        'invalid-argument',
        'Operation failed'
      );
    }

    const db = getDb();

    // =================== SECURITY: Authorization Check ===================
    // Verify the user has access to this project (owner or collaborator)
    const projectRef = db.collection('projects').doc(projectId);
    const projectDoc = await projectRef.get();

    if (!projectDoc.exists) {
      throw new HttpsError('not-found', 'Project not found');
    }

    const projectData = projectDoc.data();
    if (projectData?.ownerId !== request.auth.uid) {
      // Check if user is a collaborator
      const collaborators = projectData?.collaborators || [];
      const isCollaborator = collaborators.some(
        (c: { userId: string; role: string }) => c.userId === request.auth?.uid && c.role === 'editor'
      );
      if (!isCollaborator) {
        throw new HttpsError('permission-denied', 'User does not have access to this project');
      }
    }

    const statusRef = db.collection('projects').doc(projectId).collection('pipeline').doc('status');
    const statusDoc = await statusRef.get();

    if (!statusDoc.exists) {
      throw new HttpsError('not-found', 'Pipeline status not found');
    }

    const currentStatus = statusDoc.data() as PipelineStatus;
    const completedStages = [...(currentStatus.completedStages || [])];

    if (completedStage && !completedStages.includes(completedStage)) {
      completedStages.push(completedStage);
    }

    const updateData: Partial<PipelineStatus> & { updatedAt: FieldValue } = {
      completedStages,
      updatedAt: FieldValue.serverTimestamp(),
    };

    if (error) {
      updateData.status = 'error';
      updateData.error = error;
      updateData.completedAt = Date.now();
    } else if (nextStage) {
      updateData.currentStage = nextStage;
    } else if (completedStage === 'final') {
      // Pipeline complete
      updateData.status = 'complete';
      updateData.currentStage = null;
      updateData.completedAt = Date.now();
    }

    await statusRef.update(updateData);

    safeLog('estimatePipelineOrchestrator.log', `[PIPELINE] Updated stage for project ${projectId}: ${completedStage} -> ${nextStage || 'complete'}`);

    return {
      success: true,
      completedStages,
      currentStage: updateData.currentStage || currentStatus.currentStage,
      status: updateData.status || currentStatus.status,
    };

  } catch (error) {
    safeLog('estimatePipelineOrchestrator.error', '[PIPELINE] Error updating stage:', error);

    if (error instanceof HttpsError) {
      throw error;
    }

    throw new HttpsError(
      'internal',
      'Operation failed'
    );
  }
});
