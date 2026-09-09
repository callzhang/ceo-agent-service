import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function safeWebHref(href?: string): string | undefined {
  if (!href) return undefined;
  try {
    const parsed = new URL(href);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}

/** Safe rich-text renderer shared by agent messages and local session transcripts. */
export function MarkdownContent({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={`assistant-markdown ${className}`.trim()}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={(url) => safeWebHref(url) ?? ""}
        components={{
          a: ({ href, children }) => {
            const safe = safeWebHref(href);
            return safe
              ? <a href={safe} target="_blank" rel="noopener noreferrer">{children}</a>
              : <span>{children}</span>;
          },
          img: ({ alt }) => <span role="note">[图片已阻止：{alt || "未命名"}]</span>,
        }}
      >{text}</ReactMarkdown>
    </div>
  );
}
