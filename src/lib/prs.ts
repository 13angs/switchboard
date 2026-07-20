export interface PullRequestRef {
  key: string;
  number: number;
  url: string;
  label: string;
  state?: string | null;
}

const GITHUB_PR_URL_RE = /\bhttps:\/\/github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\/pull\/(\d+)\b/g;
const REPO_PR_RE = /(^|[^\w./-])([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)#(\d+)\b/g;

function makeRef(owner: string, repo: string, number: string | number, state?: string | null): PullRequestRef {
  const n = Number(number);
  const key = `${owner}/${repo}#${n}`;
  return {
    key,
    number: n,
    url: `https://github.com/${owner}/${repo}/pull/${n}`,
    label: key,
    state,
  };
}

export function extractPullRequests(text: string): PullRequestRef[] {
  const refs: PullRequestRef[] = [];

  for (const match of text.matchAll(GITHUB_PR_URL_RE)) {
    refs.push(makeRef(match[1], match[2], match[3]));
  }

  for (const match of text.matchAll(REPO_PR_RE)) {
    refs.push(makeRef(match[2], match[3], match[4]));
  }

  return mergePullRequests([], refs);
}

export function sessionPullRequest(
  number: number | null,
  url: string | null,
  state: string | null
): PullRequestRef[] {
  if (!number || !url) return [];
  const match = GITHUB_PR_URL_RE.exec(url);
  GITHUB_PR_URL_RE.lastIndex = 0;
  if (match) {
    return [makeRef(match[1], match[2], match[3], state)];
  }
  return [{
    key: url,
    number,
    url,
    label: `PR #${number}`,
    state,
  }];
}

export function mergePullRequests(
  current: PullRequestRef[],
  incoming: PullRequestRef[]
): PullRequestRef[] {
  const byKey = new Map<string, PullRequestRef>();
  for (const pr of [...current, ...incoming]) {
    if (!Number.isFinite(pr.number) || pr.number <= 0 || !pr.url) continue;
    byKey.set(pr.key, { ...byKey.get(pr.key), ...pr });
  }
  return [...byKey.values()].sort((a, b) => a.label.localeCompare(b.label));
}
