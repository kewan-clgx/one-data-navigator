"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type ProfileField = {
  path: string[];
  data_type: string;
  filled_rows: number;
  min_value: string | null;
  max_value: string | null;
};
type Profile = {
  sampled_at: string | null;
  sample_rows: number | null;
  fields: ProfileField[];
  job_status: "queued" | "running" | "succeeded" | "failed" | "skipped" | "cancelled" | null;
  error_code: string | null;
  credential_changed: boolean;
};

const reasons: Record<string, string> = {
  estimate_unavailable: "The scan estimate was unavailable; no query was run.",
  budget_exceeded: "The scan exceeds the table or daily budget.",
  empty_sample: "The sample contained no rows.",
  attempts_exhausted: "The job exceeded its retry limit.",
  profile_failed: "The profiling query failed. Check connection access and credentials.",
  already_profiled: "A successful profile already exists.",
};

export function useColumnProfile(connectionId: string, path: string[]) {
  const query = path.map((p) => `path=${encodeURIComponent(p)}`).join("&");
  const url = `/api/odn/connections/${encodeURIComponent(connectionId)}/column-profile?${query}`;
  const [state, setState] = useState<{ url: string; profile: Profile | null; error: string | null; disabled: boolean } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [requestUrl, setRequestUrl] = useState<string | null>(null);
  const currentUrl = useRef(url);
  currentUrl.current = url;

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const load = async () => {
      try {
        const response = await fetch(url, { signal: controller.signal, cache: "no-store" });
        if (response.status === 404 || response.status === 400) {
          if (active) setState({ url, profile: null, error: null, disabled: true });
          return;
        }
        const body = await response.json();
        if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
        if (!active) return;
        const profile = body as Profile;
        setState({ url, profile, error: null, disabled: false });
        if (profile.job_status === "queued" || profile.job_status === "running") timer = setTimeout(load, 3000);
      } catch (e) {
        if (active) setState({ url, profile: null, error: (e as Error).message, disabled: false });
      }
    };
    void load();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [url, refresh]);

  const request = useCallback(async () => {
    setRequestUrl(url);
    try {
      const response = await fetch(url, { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
      if (currentUrl.current === url) {
        setState({ url, profile: body as Profile, error: null, disabled: false });
        setRefresh((n) => n + 1);
      }
    } catch (e) {
      if (currentUrl.current === url) setState((old) => ({
        url, profile: old?.url === url ? old.profile : null, error: (e as Error).message, disabled: false,
      }));
    } finally {
      setRequestUrl((current) => current === url ? null : current);
    }
  }, [url]);

  const visible = state?.url === url ? state : null;
  return { profile: visible?.profile ?? null, error: visible?.error ?? null, disabled: visible?.disabled ?? false, requesting: requestUrl === url, request };
}

export function fieldPath(field: ProfileField): string {
  return field.path.map((part) => /[.`]/.test(part) ? JSON.stringify(part) : part).join(".");
}

export function profileField(profile: Profile | null, name: string, type: string): ProfileField | undefined {
  const normalized = (value: string) => value.toUpperCase().replace(/^INT64$/, "INTEGER").replace(/^FLOAT64$/, "FLOAT");
  return profile?.fields.find((field) =>
    field.path.length === 1 && field.path[0] === name && normalized(field.data_type) === normalized(type),
  );
}

export function fillRate(field: ProfileField | undefined, profile: Profile | null): string {
  return field && profile?.sample_rows ? `${(100 * field.filled_rows / profile.sample_rows).toFixed(1)}%` : "–";
}

export function ProfileStatus({ profile, error }: { profile: Profile | null; error: string | null }) {
  return (
    <p className="muted" aria-live="polite">
      {profile?.sampled_at
        ? `Estimated from ${profile.sample_rows?.toLocaleString()} sampled rows (10%), ${new Date(profile.sampled_at).toLocaleString()}.`
        : "No successful profile yet."}
      {profile?.job_status === "queued" && " Profile queued."}
      {profile?.job_status === "running" && " Profiling in progress."}
      {profile?.credential_changed && " Connection credentials changed; request a new profile."}
      {profile?.error_code && ` ${reasons[profile.error_code] ?? `Profile status: ${profile.error_code}.`}`}
      {error && <span className="error"> {error}</span>}
    </p>
  );
}
