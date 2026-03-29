"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";

type AnalysisResponse = Record<string, unknown>;

const CONNECTION_TEMPLATES: Record<string, string> = {
  mysql: "mysql+aiomysql://USER:PASSWORD@127.0.0.1:3306/DATABASE",
  postgres: "postgresql+asyncpg://USER:PASSWORD@127.0.0.1:5432/DATABASE",
  oracle:
    "oracle+oracledb://USER:PASSWORD@127.0.0.1:1521/?service_name=ORCL",
};

function apiBase(): string {
  return (process.env.NEXT_PUBLIC_BACKEND_URL || "").trim().replace(/\/+$/, "");
}

function apiUrl(path: string): string {
  const base = apiBase();
  const p = path.startsWith("/") ? path : `/${path}`;
  return base ? `${base}${p}` : p;
}

function formatHttpError(status: number, body: string): string {
  const t = body.trim();
  if (
    t.toLowerCase().startsWith("<!doctype") ||
    t.includes("<html") ||
    t.includes("next-head-count")
  ) {
    return [
      `HTTP ${status}：收到 Next.js 错误页（HTML），通常是本机 FastAPI 未启动或代理失败。`,
      `请确认后端已监听（默认 http://127.0.0.1:8010）后重试。`,
    ].join(" ");
  }
  return t || `HTTP ${status}`;
}

export default function HomePage() {
  const [dialect, setDialect] = useState("mysql");
  const [databaseUrl, setDatabaseUrl] = useState("");
  const [skipDb, setSkipDb] = useState(false);
  const [connOk, setConnOk] = useState(false);
  const [connMsg, setConnMsg] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  const [sql, setSql] = useState("SELECT 1 AS one");
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const template = useMemo(
    () => CONNECTION_TEMPLATES[dialect] ?? CONNECTION_TEMPLATES.mysql,
    [dialect],
  );

  const canAnalyze =
    sql.trim().length > 0 &&
    (skipDb || !databaseUrl.trim() || connOk);

  async function onTestConnection() {
    if (!databaseUrl.trim()) {
      setConnMsg("请先填写连接字符串。");
      setConnOk(false);
      return;
    }
    setTesting(true);
    setConnMsg(null);
    setConnOk(false);
    try {
      const res = await fetch(apiUrl("/api/analysis/test-connection"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dialect,
          database_url: databaseUrl.trim(),
        }),
      });
      const data = (await res.json()) as { ok?: boolean; message?: string };
      if (!res.ok) {
        throw new Error(
          typeof data.message === "string" ? data.message : `HTTP ${res.status}`,
        );
      }
      setConnOk(Boolean(data.ok));
      setConnMsg(data.message ?? (data.ok ? "成功" : "失败"));
    } catch (e) {
      setConnOk(false);
      setConnMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  }

  async function onAnalyze() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const payload: { sql: string; dialect: string; database_url?: string } = {
        sql,
        dialect,
      };
      if (!skipDb && databaseUrl.trim()) {
        payload.database_url = databaseUrl.trim();
      }
      const res = await fetch(apiUrl("/api/analysis/run"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
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
    <main className="mx-auto flex max-w-5xl flex-col gap-8 p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">SQL Doctor</h1>
        <p className="text-sm text-slate-600">
          先按数据库类型填写<strong>异步连接串</strong>并<strong>测试连接</strong>，再输入待诊断 SQL
          获取 <strong>EXPLAIN</strong> 与优化建议。勾选「跳过数据库」则仅做静态规则分析。
        </p>
      </header>

      <section className="flex flex-col gap-3 rounded-lg border border-slate-200 p-4">
        <h2 className="text-sm font-semibold text-slate-800">1. 数据库连接</h2>
        <div className="grid gap-3 md:grid-cols-[200px_1fr]">
          <div className="flex flex-col gap-2">
            <label className="text-sm font-medium" htmlFor="dialect">
              数据库类型
            </label>
            <select
              id="dialect"
              className="h-10 rounded-md border border-slate-200 px-2 text-sm"
              value={dialect}
              onChange={(e) => {
                setDialect(e.target.value);
                setConnOk(false);
                setConnMsg(null);
              }}
            >
              <option value="mysql">MySQL / MariaDB</option>
              <option value="postgres">PostgreSQL</option>
              <option value="oracle">Oracle</option>
            </select>
          </div>
          <div className="flex flex-col gap-2">
            <label className="text-sm font-medium" htmlFor="dburl">
              连接字符串（SQLAlchemy 异步）
            </label>
            <textarea
              id="dburl"
              className="min-h-[88px] rounded-md border border-slate-200 p-2 font-mono text-xs"
              placeholder={template}
              value={databaseUrl}
              onChange={(e) => {
                setDatabaseUrl(e.target.value);
                setConnOk(false);
                setConnMsg(null);
              }}
              disabled={skipDb}
            />
            <p className="text-xs text-slate-500">
              模板：<span className="font-mono text-slate-700">{template}</span>
            </p>
          </div>
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={skipDb}
            onChange={(e) => {
              setSkipDb(e.target.checked);
              if (e.target.checked) {
                setConnOk(false);
                setConnMsg(null);
              }
            }}
          />
          跳过数据库，仅静态分析（不执行 EXPLAIN）
        </label>
        {!skipDb ? (
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" onClick={onTestConnection} disabled={testing}>
              {testing ? "测试中…" : "测试连接"}
            </Button>
            {connMsg ? (
              <span
                className={
                  connOk ? "text-sm text-emerald-700" : "text-sm text-red-700"
                }
              >
                {connMsg}
              </span>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="flex flex-col gap-3 rounded-lg border border-slate-200 p-4">
        <h2 className="text-sm font-semibold text-slate-800">
          2. 待分析 SQL
        </h2>
        {connOk && !skipDb ? (
          <p className="text-sm text-emerald-800">
            连接已成功，请输入要诊断的 SQL，点击「分析」将拉取执行计划并生成建议。
          </p>
        ) : null}
        {skipDb ? (
          <p className="text-sm text-amber-800">
            已跳过数据库：仅根据语法与静态规则给出建议，无 EXPLAIN。
          </p>
        ) : null}
        <textarea
          className="min-h-[200px] rounded-md border border-slate-200 p-3 font-mono text-sm"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
        />
        <Button
          type="button"
          onClick={onAnalyze}
          disabled={loading || !canAnalyze}
        >
          {loading ? "分析中…" : "分析"}
        </Button>
        {!skipDb && databaseUrl.trim() && !connOk && sql.trim() ? (
          <p className="text-xs text-amber-700">
            已填写连接串：请先点击「测试连接」成功后再分析。
          </p>
        ) : null}
      </section>

      {error ? (
        <pre className="rounded-md bg-red-50 p-4 text-sm text-red-800">
          {error}
        </pre>
      ) : null}
      {result ? (
        <pre className="max-h-[560px] overflow-auto rounded-md bg-slate-50 p-4 text-xs">
          {JSON.stringify(result, null, 2)}
        </pre>
      ) : null}
    </main>
  );
}
