import { Toaster as SonnerToaster } from "sonner";
import { AlertCircle, AlertTriangle, Check, Info, Loader2 } from "lucide-react";

export function Toaster() {
  return (
    <SonnerToaster
      theme="dark"
      position="bottom-center"
      closeButton
      // Custom icons in our palette — sonner's defaults plus richColors paint
      // success in green, which felt loud for ambient auto-saves. Errors still
      // get red so they stay attention-grabbing.
      icons={{
        success: <Check className="h-4 w-4 text-fg" />,
        error: <AlertCircle className="h-4 w-4 text-critical" />,
        warning: <AlertTriangle className="h-4 w-4 text-warn" />,
        info: <Info className="h-4 w-4 text-info" />,
        loading: <Loader2 className="h-4 w-4 animate-spin text-muted" />,
      }}
      toastOptions={{
        classNames: {
          toast:
            "!bg-surface !text-fg !border !border-border/60 rounded-lg shadow-xl",
          closeButton:
            "!bg-surface-2 !text-muted hover:!text-fg !border-border/60",
          actionButton: "!bg-primary !text-primary-fg !rounded-md !px-3 !py-1.5",
          cancelButton: "!bg-surface-2 !text-muted",
        },
      }}
    />
  );
}
