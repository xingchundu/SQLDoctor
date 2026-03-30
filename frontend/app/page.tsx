"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";

type SuggestionItem = {
  id?: string;
  title?: string;
  detail?: string;
  severity?: string;
  rationale?: string;
};

type SuggestionsOnlyResponse = {
  dialect?: string | null;
  items?: SuggestionItem[];
};

type PlanProblemItem = {
  code?: string;
  title?: string;
  reason?: string;
  affected_steps?: number[];
};

type PlanAnalysisSummary = {
  total_steps?: number;
  total_rule_hits?: number;
  unique_rules?: number;
  risk_level?: string;
  rule_step_counts?: Record<string, number>;
};

/** POST /api/analysis/run 完整响应（suggestions_only: false） */
type FullAnalysisResponse = {
  parse?: Record<string, unknown> | null;
  plan?: Record<string, unknown> | null;
  plan_analysis?: {
    problems?: PlanProblemItem[];
    risk_level?: string;
    summary?: PlanAnalysisSummary;
    details?: string[];
  } | null;
  suggestions?: {
    dialect?: string;
    items?: SuggestionItem[];
  } | null;
  rewrite?: {
    candidates?: Array<{ title?: string; sql_text?: string; notes?: string }>;
  } | null;
};

type ChatTurn = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

function formatRulesReply(data: SuggestionsOnlyResponse): string {
  const items = data.items ?? [];
  if (items.length === 0) {
    return "未生成结构化建议条目。请确认 SQL 非空且后端正常。";
  }
  const parts = items.map((it, i) => {
    const sev = it.severity ? ` · ${it.severity}` : "";
    return `**${i + 1}. ${it.title ?? "建议"}**${sev}\n\n${it.detail ?? ""}`;
  });
  return parts.join("\n\n---\n\n");
}

/** 按 rationale + title 去重，避免摘要与详情重复罗列 */
function dedupeSuggestionItems(items: SuggestionItem[]): SuggestionItem[] {
  const seen = new Set<string>();
  const out: SuggestionItem[] = [];
  for (const it of items) {
    const k = `${it.rationale ?? ""}::${it.title ?? ""}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(it);
  }
  return out;
}

function truncateText(s: string, maxLen: number): string {
  if (s.length <= maxLen) return s;
  return `${s.slice(0, maxLen)}\n…（已截断）`;
}

/** 确定性流水线：sqlglot → EXPLAIN → 计划规则 + 建议（不经过 RAG 大模型 JSON） */
function formatSqlPipelineReport(data: FullAnalysisResponse): string {
  const blocks: string[] = [];
  blocks.push(
    "**SQL 诊断报告**（① sqlglot → ② EXPLAIN → ③ 计划规则 → ④ 静态/综合建议）",
  );

  const cand = data.rewrite?.candidates?.[0];
  const fromRewrite = (cand?.sql_text ?? "").trim();
  const fromParse =
    typeof data.parse?.normalized_sql === "string"
      ? data.parse.normalized_sql.trim()
      : "";
  const sqlBlock = fromRewrite || fromParse || "（sqlglot 未产出或解析失败）";
  blocks.push(
    "### ① sqlglot 解析 / 格式化 SQL\n\n```sql\n" + sqlBlock + "\n```",
  );

  const plan = data.plan;
  if (plan && typeof plan === "object" && plan.skipped === true) {
    blocks.push("### ② EXPLAIN\n\n（未执行：无可用数据库连接）");
  } else if (plan && typeof plan === "object" && plan.raw_rows != null) {
    blocks.push(
      "### ② EXPLAIN（数据库 raw_rows）\n\n```json\n" +
        truncateText(JSON.stringify(plan.raw_rows, null, 2), 14000) +
        "\n```",
    );
  } else {
    blocks.push("### ② EXPLAIN\n\n（无 raw_rows）");
  }

  const pa = data.plan_analysis;
  if (
    pa &&
    ((pa.problems && pa.problems.length > 0) ||
      (pa.summary && Object.keys(pa.summary).length > 0) ||
      (pa.details && pa.details.length > 0))
  ) {
    const sum = pa.summary;
    if (sum && (sum.total_steps != null || sum.unique_rules != null)) {
      const lines: string[] = [
        "### ③ 执行计划风险摘要（MySQL / PostgreSQL / Oracle 统一规则）",
        "",
        `- **综合风险**：${sum.risk_level ?? pa.risk_level ?? "—"}`,
        `- **计划步骤数**：${sum.total_steps ?? "—"}`,
        `- **规则种类**：${sum.unique_rules ?? "—"}（累计触发 ${sum.total_rule_hits ?? "—"} 次）`,
      ];
      const rc = sum.rule_step_counts;
      if (rc && Object.keys(rc).length > 0) {
        lines.push(
          `- **类型分布**：${Object.entries(rc)
            .map(([k, v]) => `${k}×${v}`)
            .join("、")}`,
        );
      }
      blocks.push(lines.join("\n"));
    }
    if (pa.details && pa.details.length > 0) {
      blocks.push(
        ["### ③‑附 简要说明", "", ...pa.details.map((d) => `- ${d}`)].join(
          "\n",
        ),
      );
    }
    if (pa.problems && pa.problems.length > 0) {
      const lines: string[] = ["### ③‑详情 执行计划问题（按规则合并，不重复）"];
      pa.problems.forEach((p, i) => {
        const code = p.code ? ` \`${p.code}\`` : "";
        const steps =
          Array.isArray(p.affected_steps) && p.affected_steps.length > 0
            ? `影响步骤：${p.affected_steps.map((s) => `step${s}`).join("、")}`
            : "";
        lines.push(
          "",
          `${i + 1}. **${p.title ?? "项"}**${code}`,
          steps,
          "",
          p.reason ?? "",
        );
      });
      blocks.push(lines.join("\n"));
    }
  }

  const items = dedupeSuggestionItems(data.suggestions?.items ?? []);
  if (items.length > 0) {
    blocks.push(
      "### ④ 静态 AST + 计划综合建议（已去重）\n\n" +
        formatRulesReply({
          dialect: data.suggestions?.dialect ?? null,
          items,
        }),
    );
  }

  return blocks.join("\n\n---\n\n");
}

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

function newId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** 判定本轮输入是否按 SQL 走 EXPLAIN/诊断；否则走中文对话接口 */
function isLikelySql(input: string): boolean {
  const t = input.trim();
  if (!t) return false;
  const sqlKeyword =
    /\b(select|insert|update|delete|merge|with|truncate|explain)\b/i.test(t) ||
    /\b(create|alter|drop)\s+(table|index|view|database)\b/i.test(t) ||
    /\b(show|desc|describe)\s+/i.test(t);
  if (sqlKeyword) return true;
  if (/from\s+[`"\w\[\]]+/i.test(t) && /\bwhere\b/i.test(t)) return true;
  if (/^\s*\(?\s*select\b/i.test(t)) return true;
  const cjk = (t.match(/[\u4e00-\u9fff\u3000-\u303f]/g) ?? []).length;
  if (cjk > 0 && cjk / t.length >= 0.25) return false;
  if (/[=;]/.test(t) && /\bfrom\b/i.test(t)) return true;
  return false;
}

export default function HomePage() {
  const [dialect, setDialect] = useState("mysql");
  const [databaseUrl, setDatabaseUrl] = useState("");
  const [skipDb, setSkipDb] = useState(false);
  const [connOk, setConnOk] = useState(false);
  const [connMsg, setConnMsg] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  const [chatMessages, setChatMessages] = useState<ChatTurn[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const template = useMemo(
    () => CONNECTION_TEMPLATES[dialect] ?? CONNECTION_TEMPLATES.mysql,
    [dialect],
  );

  const canSendChat =
    chatInput.trim().length > 0 &&
    (!isLikelySql(chatInput.trim()) ||
      skipDb ||
      !databaseUrl.trim() ||
      connOk);

  /** 已测通连接：SQL 走完整工具链（含 EXPLAIN），仍为确定性流水线，不经 RAG/大模型 */
  const useDbExplainPath =
    !skipDb && databaseUrl.trim().length > 0 && connOk;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, chatLoading]);

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

  async function onSendChat() {
    const text = chatInput.trim();
    if (!text || !canSendChat || chatLoading) return;

    setChatInput("");
    setChatMessages((prev) => [
      ...prev,
      { id: newId(), role: "user", content: text },
    ]);
    setChatLoading(true);

    try {
      let assistantContent: string;

      if (!isLikelySql(text)) {
        const messagesForNl = [
          ...chatMessages.map((m) => ({
            role: m.role as "user" | "assistant",
            content: m.content,
          })),
          { role: "user" as const, content: text },
        ];
        const res = await fetch(apiUrl("/api/rag/nl-chat"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages: messagesForNl, dialect }),
        });
        if (!res.ok) {
          const raw = await res.text();
          throw new Error(formatHttpError(res.status, raw));
        }
        const data = (await res.json()) as { reply?: string };
        assistantContent =
          (typeof data.reply === "string" && data.reply) || "（无回复）";
      } else if (useDbExplainPath) {
        const res = await fetch(apiUrl("/api/analysis/run"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sql: text,
            dialect,
            database_url: databaseUrl.trim(),
            suggestions_only: false,
          }),
        });
        if (!res.ok) {
          const raw = await res.text();
          throw new Error(formatHttpError(res.status, raw));
        }
        const data = (await res.json()) as FullAnalysisResponse;
        assistantContent = formatSqlPipelineReport(data);
      } else {
        const payload: {
          sql: string;
          dialect: string;
          suggestions_only: boolean;
          database_url?: string;
        } = {
          sql: text,
          dialect,
          suggestions_only: true,
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
          const raw = await res.text();
          throw new Error(formatHttpError(res.status, raw));
        }
        const data = (await res.json()) as SuggestionsOnlyResponse;
        assistantContent =
          "**（规则引擎，未走本地大模型）**\n\n" + formatRulesReply(data);
      }

      setChatMessages((prev) => [
        ...prev,
        { id: newId(), role: "assistant", content: assistantContent },
      ]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setChatMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          content: `**请求失败**\n\n${msg}`,
        },
      ]);
    } finally {
      setChatLoading(false);
    }
  }

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-8 p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">SQL Doctor</h1>
        <p className="text-sm text-slate-600">
          <strong>中文或日常提问</strong>走对话接口（不调 EXPLAIN）；识别为 <strong>SQL</strong>{" "}
          时走解析与（可选）EXPLAIN、执行计划规则、建议与 <strong>sqlglot</strong> 格式化改写，均为确定性工具链。
          已测通连接才会执行 EXPLAIN。需要 <strong>FAISS + 大模型</strong> 综合诊断请用{" "}
          <code className="rounded bg-slate-100 px-1">POST /api/rag/diagnose</code> 或 README 中的
          Streamlit 流程。
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

      <section className="flex flex-col gap-3 rounded-lg border border-slate-200 p-0 overflow-hidden">
        <div className="border-b border-slate-200 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-800">
            2. 对话优化
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            中文对话走本地模型。SQL 语句：{" "}
            {useDbExplainPath
              ? "将走 EXPLAIN + 规则分析 + 改写（工具链，无 RAG）。"
              : skipDb
                ? "当前为规则引擎（无 EXPLAIN）。"
                : databaseUrl.trim() && !connOk
                  ? "已填连接串时请先「测试连接」再发 SQL。"
                  : "未填连接串时 SQL 仅规则建议。"}
          </p>
        </div>

        <div className="flex max-h-[min(560px,70vh)] min-h-[320px] flex-col bg-slate-50">
          <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
            {chatMessages.length === 0 ? (
              <p className="py-10 text-center text-sm text-slate-500">
                可发中文讨论优化思路，或直接粘贴 SQL。SQL 诊断在已连接库时带上 EXPLAIN；纯中文走对话接口。
              </p>
            ) : null}
            {chatMessages.map((m) => (
              <div
                key={m.id}
                className={
                  m.role === "user"
                    ? "flex justify-end"
                    : "flex justify-start"
                }
              >
                <div
                  className={
                    m.role === "user"
                      ? "max-w-[min(100%,36rem)] rounded-2xl rounded-br-md bg-slate-800 px-4 py-2.5 text-sm text-white"
                      : "max-w-[min(100%,36rem)] rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-800 shadow-sm"
                  }
                >
                  <p className="mb-1 text-[10px] font-medium uppercase tracking-wide opacity-70">
                    {m.role === "user" ? "你" : "助手"}
                  </p>
                  <div className="whitespace-pre-wrap break-words leading-relaxed">
                    {m.role === "user" ? (
                      m.content
                    ) : (
                      <AssistantMarkdown text={m.content} />
                    )}
                  </div>
                </div>
              </div>
            ))}
            {chatLoading ? (
              <div className="flex justify-start">
                <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 shadow-sm">
                  正在分析…
                </div>
              </div>
            ) : null}
            <div ref={messagesEndRef} />
          </div>

          <div className="border-t border-slate-200 bg-white p-3">
            {!skipDb && databaseUrl.trim() && !connOk ? (
              <p className="mb-2 text-xs text-amber-700">
                发送 <strong>SQL</strong> 前请先「测试连接」；仅中文对话可直接发送。
              </p>
            ) : null}
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
              <textarea
                className="min-h-[88px] flex-1 resize-y rounded-xl border border-slate-200 px-3 py-2 font-mono text-sm outline-none focus:ring-2 focus:ring-slate-300"
                placeholder="中文提问，或粘贴 SQL（含 select/from/where 等）…"
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                disabled={chatLoading}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    canSendChat &&
                    !chatLoading
                  ) {
                    e.preventDefault();
                    void onSendChat();
                  }
                }}
              />
              <Button
                type="button"
                className="shrink-0 sm:w-28"
                onClick={() => void onSendChat()}
                disabled={chatLoading || !canSendChat}
              >
                {chatLoading ? "发送中…" : "发送"}
              </Button>
            </div>
            <p className="mt-2 text-[11px] text-slate-400">
              Enter 发送，Shift+Enter 换行。本地模型需在服务端 .env 配置 LLM_MODEL 与
              OLLAMA_BASE_URL（或等价 OpenAI 兼容地址）。
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}

/** 极简 Markdown 子集：**粗体** 与 ```sql 代码块，避免引入依赖 */
function AssistantMarkdown({ text }: { text: string }) {
  const parts = text.split(/(```[\s\S]*?```)/g);
  return (
    <>
      {parts.map((part, i) => {
        const fence = part.match(/^```(\w*)\n?([\s\S]*?)```$/);
        if (fence) {
          const code = fence[2] ?? "";
          return (
            <pre
              key={i}
              className="my-2 overflow-x-auto rounded-lg bg-slate-900 p-3 font-mono text-xs text-slate-100"
            >
              {code.trimEnd()}
            </pre>
          );
        }
        return <InlineBold key={i} text={part} />;
      })}
    </>
  );
}

function InlineBold({ text }: { text: string }) {
  const segments = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {segments.map((seg, i) => {
        const m = seg.match(/^\*\*([^*]+)\*\*$/);
        if (m) {
          return (
            <strong key={i} className="font-semibold text-slate-900">
              {m[1]}
            </strong>
          );
        }
        return <span key={i}>{seg}</span>;
      })}
    </>
  );
}
