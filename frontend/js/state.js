// Persisted session state (survives refresh / browser restart / another
// authorized device logging in with the same token). The browser is a
// controller/viewer only -- the server remains the authority on every
// decision; this module just remembers which job/token the user was using.

const TOKEN_KEY = "ds_review_token";
const JOB_KEY = "ds_review_job_id";

export function loadSession() {
  return {
    token: localStorage.getItem(TOKEN_KEY) || "",
    jobId: localStorage.getItem(JOB_KEY) || "",
  };
}

export function saveToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function saveJobId(jobId) {
  if (jobId) localStorage.setItem(JOB_KEY, jobId);
  else localStorage.removeItem(JOB_KEY);
}
