"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";

type AnalysisResponse = Record<string, unknown>;

function analysisRunUrl(): string {
  const base = (process.env.NEXT_PUBLIC_BACKEND_URL || "").trim().replace(/\/+$/, "");
  if (base) return `${base}/api/analysis/run`;
  return "/api/analysis/run";
}

function formatHttpError(status: number, body: string): string {
  const t = body.trim();
  if (
    t.toLowerCase().startsWith("<!doctype") ||
    t.includes("<html") ||
    t.includes("next-head-count")
  ) {
    return [
      `HTTP ${status}：收到 Next.js 错误页（HTML），通常是本机 FastAPI 未启动或 rewrite 代理失败。`,
      `请确认后端已监听（默认 http://127.0.0.1:8010），开发模式下页面已默认直连该地址；若仍失败请查浏览器 Network 与后端终端日志。`,
    ].join(" ");
  }
  return t || `HTTP ${status}`;
}

export default function HomePage() {
  const [sql, setSql] = useState("SELECT 1 AS one");
  const [dialect, setDialect] = useState("mysql");
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onAnalyze() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch(analysisRunUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql, dialect }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(formatHttpError(res.status, text));
      }
      const data = (await res.json()) as AnalysisResponse;
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">SQL Doctor</h1>
        <p className="text-sm text-slate-600">
          开发环境默认直连 FastAPI（<code className="rounded bg-slate-100 px-1">NEXT_PUBLIC_BACKEND_URL</code> /
          <code className="rounded bg-slate-100 px-1">API_PORT</code>）；生产可设{" "}
          <code className="rounded bg-slate-100 px-1">NEXT_PUBLIC_BACKEND_URL</code> 或沿用同源{" "}
          <code className="rounded bg-slate-100 px-1">/api</code> 反代。
        </p>
      </header>
      <section className="grid gap-4 md:grid-cols-[1fr_160px]">
        <div className="flex flex-col gap-2">
          <label className="text-sm font-medium" htmlFor="sql">
            SQL
          </label>
          <textarea
            id="sql"
            className="min-h-[200px] rounded-md border border-slate-200 p-3 font-mono text-sm"
            value={sql}
            onChange={(e) => setSql(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label className="text-sm font-medium" htmlFor="dialect">
            Dialect
          </label>
          <select
            id="dialect"
            className="h-10 rounded-md border border-slate-200 px-2 text-sm"
            value={dialect}
            onChange={(e) => setDialect(e.target.value)}
          >
            <option value="mysql">mysql</option>
            <option value="postgres">postgres</option>
            <option value="oracle">oracle</option>
          </select>
          <Button type="button" onClick={onAnalyze} disabled={loading}>
            {loading ? "分析中…" : "分析"}
          </Button>
        </div>
      </section>
      {error ? (
        <pre className="rounded-md bg-red-50 p-4 text-sm text-red-800">
          {error}
        </pre>
      ) : null}
      {result ? (
        <pre className="max-h-[480px] overflow-auto rounded-md bg-slate-50 p-4 text-xs">
          {JSON.stringify(result, null, 2)}
        </pre>
      ) : null}
    </main>
  );
}
