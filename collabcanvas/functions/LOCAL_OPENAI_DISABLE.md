# Disable OpenAI in local price comparison

`DISABLE_OPENAI=true` disables OpenAI client construction in `selectBestMatch`,
`generateProductMetadata`, `selectBestGlobalMatch`, and `validateGlobalMatch`.
Parsing trims whitespace and accepts `true`, `1`, `yes`, and `on`, ignoring case.
Other values retain the existing key-dependent behavior. The flag is evaluated
on every call, before accessing the credential. Existing fallbacks are unchanged.

This guard covers the comparison path, not other independently invoked Firebase
functions that use OpenAI. It does not change Python's Gemini provider or A2A URL.

## Local setup (user action, before a later approved emulator restart)

1. From `collabcanvas`, verify both local files are ignored and untracked:

   ```sh
   git check-ignore -v functions/.env.local functions/.secret.local
   git ls-files -- functions/.env.local functions/.secret.local
   ```

   Stop if either file is not ignored or the second command lists either file.
   Do not display existing file contents: they may contain credentials.

2. In the ignored `functions/.env.local`, set `DISABLE_OPENAI=true`.
   In the ignored `functions/.secret.local`, set both:

   ```dotenv
   DISABLE_OPENAI=true
   OPENAI_API_KEY=<choose-a-nonempty-nonsecret-local-sentinel>
   ```

   Replace the placeholder with an arbitrary non-secret sentinel, never a real
   credential. Keep these values only in ignored local files. Preserve unrelated
   configuration. Ensure these are the effective final definitions if duplicate
   keys exist. The flag is repeated in the secrets file because resolved secrets
   are merged after normal environment configuration.

   Firebase's installed resolver fetches a bound secret when its local override
   is falsy. A non-empty sentinel prevents lookup of the Node `OPENAI_API_KEY`
   secret. An empty value is insufficient. The application guard prevents the
   sentinel from reaching any of the four OpenAI constructors. Other bound
   secrets may still be resolved; this does not disable Secret Manager globally.

3. Build Node functions, then restart the Firebase emulator using the existing
   suite launch procedure when separately authorized. An already-running worker
   is not proof that it loaded the new build or local settings.

4. Leave the independently running Python server on port 5003 unchanged:
   Gemini remains its provider and Python A2A remains on port 5003. Confirm the
   effective Node disable flag before authorizing a controlled pipeline run.

Neither local credential file is created or populated by the implementation.
Do not run a pipeline or a standalone LLM request as part of this setup.
