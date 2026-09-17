import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

// text-base (16px) on mobile prevents iOS Safari's auto-zoom on focus.
// sm:text-sm downshifts back to 14px on desktop where no zoom-trigger exists.
const base =
  "w-full bg-surface-2 border border-border/60 rounded-md px-3 text-base sm:text-sm text-fg placeholder:text-subtle " +
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, type = "text", ...props }, ref) {
    return (
      <input
        ref={ref}
        type={type}
        className={cn(base, "h-11 sm:h-10", className)}
        {...props}
      />
    );
  }
);

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return (
      <textarea
        ref={ref}
        className={cn(base, "py-2 min-h-[88px] sm:min-h-[72px] resize-y", className)}
        {...props}
      />
    );
  }
);

export function Field({
  label,
  htmlFor,
  children,
  hint,
}: {
  label: string;
  htmlFor?: string;
  children: React.ReactNode;
  hint?: string;
}) {
  // min-w-0 prevents intrinsic content (e.g. iOS date-picker chrome) from
  // forcing the field wider than its grid/flex parent, which made date inputs
  // overflow the surrounding card on mobile.
  return (
    <label htmlFor={htmlFor} className="flex flex-col gap-1.5 min-w-0">
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-subtle">{hint}</span>}
    </label>
  );
}
