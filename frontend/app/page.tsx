"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";

type AnalysisResponse = Record<string, unknown>;

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
      const res = await fetch("/api/analysis/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql, dialect }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
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
          Next.js 通过 rewrite 代理到 FastAPI（默认 127.0.0.1:8010，环境变量 API_PORT）。
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
