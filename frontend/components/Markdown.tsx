import clsx from "clsx";
import { Children, cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { isSafeUrl } from "@/lib/format";

export interface MarkdownProps {
  children: string;
  /** "memo" = full research-memo typography; "compact" = card-sized. */
  variant?: "memo" | "compact";
  className?: string;
}

const CITATION = /(\[S\d+\])/g;

/** Turn inline "[S3]" citation markers into anchored superscripts. */
function withCitations(children: ReactNode): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child === "string") {
      if (!CITATION.test(child)) return child;
      CITATION.lastIndex = 0;
      return child.split(CITATION).map((part, i) =>
        /^\[S\d+\]$/.test(part) ? (
          <sup key={i} className="ml-[1px] font-mono text-[0.68em] text-accent">
            <a href={`#source-${part.slice(1, -1)}`} className="no-underline hover:underline">
              {part.slice(1, -1)}
            </a>
          </sup>
        ) : (
          part
        ),
      );
    }
    if (isValidElement<{ children?: ReactNode }>(child) && child.props.children) {
      return cloneElement(child as ReactElement<{ children?: ReactNode }>, undefined, withCitations(child.props.children));
    }
    return child;
  });
}

function components(compact: boolean): Components {
  return {
    h1: ({ children }) => (
      <h1 className={clsx("font-serif text-ink", compact ? "mb-2 text-[20px]" : "mb-4 mt-2 text-[34px] leading-[1.1] tracking-[-0.01em]")}>
        {children}
      </h1>
    ),
    h2: ({ children }) => (
      <h2 className={clsx("font-serif text-ink", compact ? "mb-1.5 mt-4 text-[17px]" : "mb-3 mt-10 border-t border-ink pt-3 text-[23px] leading-tight")}>
        {children}
      </h2>
    ),
    h3: ({ children }) => <h3 className="label mb-2 mt-6 text-ink">{children}</h3>,
    h4: ({ children }) => <h4 className="mb-1 mt-4 text-[14px] font-semibold text-ink">{children}</h4>,
    p: ({ children }) => (
      <p className={clsx("text-ink", compact ? "mb-2 text-[13.5px] leading-relaxed" : "mb-4 max-w-[68ch] text-[15.5px] leading-[1.7]")}>
        {withCitations(children)}
      </p>
    ),
    ul: ({ children }) => <ul
        className={clsx(
          "mb-4 max-w-[68ch] list-none space-y-1.5 pl-0 [&>li]:relative [&>li]:pl-4 [&>li]:before:absolute [&>li]:before:left-0 [&>li]:before:text-ink-3 [&>li]:before:content-['–']",
          compact && "mb-2",
        )}
      >{children}</ul>,
    ol: ({ children }) => <ol className="mb-4 max-w-[68ch] list-decimal space-y-1.5 pl-6 marker:font-mono marker:text-[12px] marker:text-ink-3">{children}</ol>,
    li: ({ children }) => (
      <li className={clsx("text-ink", compact ? "text-[13.5px]" : "text-[15px] leading-[1.6]")}>{withCitations(children)}</li>
    ),
    a: ({ href, children }) => {
      if (href?.startsWith("#") || href?.startsWith("/")) {
        return <a href={href} className="link">{children}</a>;
      }
      return isSafeUrl(href) ? (
        <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="link">
          {children}
        </a>
      ) : (
        <span>{children}</span>
      );
    },
    blockquote: ({ children }) => (
      <blockquote className="my-5 max-w-[64ch] border-l-2 border-accent pl-4 font-serif text-[17px] italic leading-relaxed text-ink">
        {children}
      </blockquote>
    ),
    hr: () => <hr className="my-8 border-rule" />,
    strong: ({ children }) => <strong className="font-semibold text-ink">{children}</strong>,
    em: ({ children }) => <em className="font-serif text-[1.05em] italic">{children}</em>,
    code: ({ children, className }) =>
      className ? (
        <code className={clsx("font-mono text-[12px]", className)}>{children}</code>
      ) : (
        <code className="rounded-xs bg-panel px-1 py-[1px] font-mono text-[0.86em]">{children}</code>
      ),
    pre: ({ children }) => <pre className="mb-4 overflow-x-auto border border-rule bg-panel p-3 font-mono text-[12px] leading-relaxed">{children}</pre>,
    table: ({ children }) => (
      <div className="mb-5 overflow-x-auto">
        <table className="w-full border-collapse border-y border-ink text-left text-[13px]">{children}</table>
      </div>
    ),
    th: ({ children }) => <th className="label border-b border-rule px-2 py-1.5 font-normal text-ink">{children}</th>,
    td: ({ children }) => <td className="tnum border-b border-rule px-2 py-1.5 align-top text-ink">{withCitations(children)}</td>,
    img: () => null,
  };
}

/** Render memo/brief markdown (GFM) as a research publication. Raw HTML is not rendered. */
export function Markdown({ children, variant = "memo", className }: MarkdownProps) {
  return (
    <div className={clsx("min-w-0", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components(variant === "compact")} skipHtml>
        {children}
      </ReactMarkdown>
    </div>
  );
}
