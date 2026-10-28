import { Fragment, memo, useState } from "react";
import type { ReadingRun, ReadingView } from "./api";

function Run({
  run,
  start,
  end,
}: {
  run: ReadingRun;
  start?: number;
  end?: number;
}) {
  const [pageOpen, setPageOpen] = useState(false);
  if (run.type === "annotation") return null;
  if (run.type === "page")
    return (
      <span
        className="page-anchor"
        data-source-start={run.source_char_start}
        data-source-end={run.source_char_end}
      >
          <button type="button" className="page-reference-button" aria-label={`إحالة ${run.text}`} aria-expanded={pageOpen} onClick={()=>setPageOpen(value=>!value)} onKeyDown={event=>{if(event.key==='Escape')setPageOpen(false);}}>
            <bdi>{run.is_unknown ? "صفحة غير محددة" : run.text}</bdi>
          </button>
          {pageOpen && <span className="page-popover" role="note">
            <bdi>{run.raw_marker ?? run.text}</bdi>
            <br />
            {run.is_unknown
              ? "علامة صفرية؛ لا تعيّن صفحة."
              : "علامة OpenITI خام، غير محققة طباعيًا."}
          </span>}
      </span>
    );
  if (run.type === "virtual_space") return <Fragment>{run.text}</Fragment>;
  const a = run.source_char_start;
  const b = run.source_char_end;
  const selected =
    start !== undefined && end !== undefined && start < b && end > a;
  if (!selected)
    return (
      <span data-source-start={a} data-source-end={b}>
        {run.text}
      </span>
    );
  const chars = Array.from(run.text);
  const left = Math.max(0, start - a);
  const right = Math.min(chars.length, end - a);
  return (
    <span data-source-start={a} data-source-end={b}>
      {chars.slice(0, left).join("")}
      <mark data-source-start={a + left} data-source-end={a + right}>
        {chars.slice(left, right).join("")}
      </mark>
      {chars.slice(right).join("")}
    </span>
  );
}

export const FormattedText = memo(function FormattedText({
  view,
  start,
  end,
  className = "",
}: {
  view: ReadingView;
  start?: number;
  end?: number;
  className?: string;
}) {
  return (
    <div className={`formatted-text ${className}`} data-testid="formatted-text">
      {view.blocks.map((block) => {
        const children = block.runs.map((run, i) => (
          <Run
            key={`${i}-${run.source_char_start}`}
            run={run}
            start={start}
            end={end}
          />
        ));
        const props = {
          "data-source-start": block.source_char_start,
          "data-source-end": block.source_char_end,
        };
        return block.type === "heading" ? (
          <h2
            key={block.id}
            {...props}
            className={`reading-heading level-${block.level ?? 1}`}
          >
            {children}
          </h2>
        ) : (
          <p key={block.id} {...props}>
            {children}
          </p>
        );
      })}
    </div>
  );
});

export function citationFor(
  sourceId?: number,
  start?: number,
  end?: number,
  page?: string,
) {
  return `المصدر ${sourceId ?? "غير محدد"} · النطاق [${start ?? "?"}, ${end ?? "?"})${page ? ` · ${page} (غير محققة طباعيًا)` : ""}`;
}
