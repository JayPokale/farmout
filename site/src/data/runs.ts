// Verbatim output of real farmout runs, replayed by the Terminal section. Not
// mock-ups: paste a new run's stdout here to update the page.
//
// review: `farmout review` on bench/cases/files_api (a 61-line change with five
//   planted bugs) as an uncommitted change in a throwaway repo, four workers.
// race:   `farmout race` on a two-file repo whose retry() fails three tests.
// Both: farmout 0.1.0 (with the review merge fixed while recording these), Arch Linux, agy 1.4.6, codex-cli 0.163.0, Claude Code
// 2.1.323, Copilot CLI 1.0.112, 2026-11-17. COLUMNS=120.

export type Run = {
  id: string;
  label: string;
  cmd: string;
  secs: number;
  out: string;
  note: string;
};

export const RUNS: Run[] = [
  {
    id: "review",
    label: "review",
    cmd: "farmout review",
    secs: 44,
    note: "files_api.py, a 61-line change with five planted bugs, reviewed by four subscriptions at once. All five planted bugs are there, three flagged by every model, plus a real race in the quota update that nobody planted, which all four caught. Each row is one defect with how many models flagged it, and a finding that cited a missing file or line would have been dropped. Every reviewer is now a session you can question: farmout send review-1-codex \"…\".",
    out: `review-1: 4 reviewers (antigravity, codex, claude, copilot) on uncommitted changes
agree  severity  where            finding                                                       flagged by
4/4    high      files_api.py:18  SQL injection exposes every user’s file metadata              antigravity, codex, claude, copilot
4/4    high      files_api.py:27  Pages documented as 1-based but start = page * PAGE_SIZE sk…  antigravity, codex, claude, copilot
4/4    high      files_api.py:36  Path traversal vulnerability allows arbitrary filesystem re…  antigravity, codex, claude, copilot
4/4    medium    files_api.py:48  Lost update / race condition in quota decrement               antigravity, codex, claude, copilot
3/4    high      files_api.py:43  Unhandled None crash (TypeError) when file does not exist     antigravity, claude, copilot
2/4    medium    files_api.py:49  No commit, and non-atomic ordering of quota update, file re…  antigravity, claude
1/4    high      files_api.py:48  Unhandled None crash (TypeError) or missing quota record      antigravity
1/4    medium    files_api.py:25  No lower bound on page                                        claude
1/4    medium    files_api.py:47  Path traversal in delete via stored name/owner                claude
1/4    low       files_api.py:23  f["url"] built from unescaped/unencoded name and owner        claude
Follow up with a reviewer: farmout send review-1-<worker> "..."`,
  },
  {
    id: "race",
    label: "race",
    cmd: 'farmout race "Make the tests in test_retry.py pass" --test "python3 -m unittest"',
    secs: 61,
    note: "retry() with three failing tests: one attempt too few, the last error swallowed, a sleep after the final failure. Each subscription fixed it in its own git worktree; all four patches passed, so the smallest diff and then the fastest won. The repository's own tree had no changes afterwards, until farmout apply.",
    out: `worker       result  tests  files  +/-    time  session
antigravity  ok      pass   1      +3/-2  60s   race-1-antigravity
codex        ok      pass   1      +3/-2  28s   race-1-codex
claude       ok      pass   1      +3/-2  15s   race-1-claude
copilot      ok      pass   1      +3/-2  25s   race-1-copilot

Winner: race-1-claude (claude). See it: farmout diff race-1-claude   Apply it: farmout apply race-1-claude`,
  },
];
