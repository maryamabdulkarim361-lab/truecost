/** Runtime guard for the comparison paths; never reads the key when disabled. */
export function isOpenAIEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  const disabled = ['true', '1', 'yes', 'on'].includes(
    (env.DISABLE_OPENAI || '').trim().toLowerCase()
  );
  return !disabled && Boolean(env.OPENAI_API_KEY);
}
