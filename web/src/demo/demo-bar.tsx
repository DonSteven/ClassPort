import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Check, Globe, Palette, Play, RotateCcw, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { useCreateTask } from "@/lib/queries";
import { setLanguage, markLanguageExplicit } from "@/lib/i18n";
import { addEasternDays, fromEasternInput, fmtIsoDate } from "@/lib/time";
import { resetDemoData } from "./store";
import "./demo.css";

export default function DemoBar() {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const createTask = useCreateTask();

  function reset() {
    resetDemoData();
    window.location.assign("/app");
  }

  async function runExample() {
    try {
      const task = await createTask.mutateAsync({
        course_code: "STAT201", title: "Compare the Gamma distribution with its normal approximation",
        description: "Created by the preset MCP demonstration. Read course-notes.md, then plot the distributions for several sample sizes.",
        priority: "high", status: "open", tags: ["MCP example"],
        due_at: fromEasternInput(`${fmtIsoDate(addEasternDays(new Date(), 1))}T16:00`),
      });
      setTaskId(task.id);
      toast.success(t("demo.taskCreated"));
    } catch (error) {
      toast.error((error as Error).message);
    }
  }

  return (
    <>
      <div className="demo-bar" role="region" aria-label={t("demo.label")}>
        <Link to="/" className="demo-bar-brand" aria-label={t("demo.label")}><Sparkles size={15} /><strong>{t("demo.label")}</strong><span className="demo-short-label">{t("demo.shortLabel")}</span></Link>
        <span className="demo-bar-description">{t("demo.description")}</span>
        <div className="demo-bar-actions">
          <Link to="/app/settings" title={t("demo.themes")}><Palette size={15} /><span>{t("demo.themes")}</span></Link>
          <button type="button" onClick={() => { markLanguageExplicit(); setLanguage(i18n.language === "zh" ? "en" : "zh"); }} aria-label={t("demo.language")}><Globe size={15} />{i18n.language === "zh" ? "EN" : "中文"}</button>
          <button type="button" onClick={() => setOpen(true)}><Play size={14} /><span>{t("demo.mcpExample")}</span></button>
          <button type="button" onClick={reset} aria-label={t("demo.reset")} title={t("demo.reset")}><RotateCcw size={14} /><span>{t("demo.reset")}</span></button>
        </div>
      </div>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent title={t("demo.mcpTitle")} description={t("demo.mcpDescription")} className="sm:max-w-xl">
          <p className="text-sm text-muted mb-5">{t("demo.mcpIntro")}</p>
          <ol className="demo-conversation">
            <li><span className="demo-speaker">{t("demo.visitor")}</span><p>{t("demo.prompt")}</p></li>
            <li><span className="demo-speaker">{t("demo.preparedRead")}</span><p>{t("demo.explanation")}</p><Link to="/app/files" onClick={() => setOpen(false)}>{t("demo.openNotes")} <ArrowRight size={13} /></Link></li>
            <li><span className="demo-speaker">{t("demo.preparedWrite")}</span><p>{t("demo.followUp")}</p></li>
          </ol>
          <p className="mt-4 text-xs text-muted">{t("demo.simulation")}</p>
          {taskId ? (
            <Link to="/app/tasks" onClick={() => setOpen(false)} className="demo-run mt-5"><Check size={16} />{t("demo.viewTask")}<ArrowRight size={15} /></Link>
          ) : (
            <button type="button" onClick={runExample} disabled={createTask.isPending} className="demo-run mt-5"><Play size={15} />{t("demo.run")}</button>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
