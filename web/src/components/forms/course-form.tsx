import { useEffect, useState, type FormEvent } from "react";
import { Loader2, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input, Textarea, Field } from "@/components/ui/input";
import type { Course } from "@/data/types";
import {
  useCreateCourse,
  useDeleteCourse,
  useFilesList,
  useUpdateCourse,
} from "@/lib/queries";
import { fallbackAccent } from "@/lib/theme";
import { cn } from "@/lib/cn";
import { toast } from "sonner";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  course?: Course | null; // present → edit; absent → create
};

const CODE_PATTERN = /^[A-Z0-9]{1,8}$/;

export function CourseForm({ open, onOpenChange, course }: Props) {
  const { t } = useTranslation();
  const editing = Boolean(course);
  const [code, setCode] = useState("");
  const [fullName, setFullName] = useState("");
  const [moduleCode, setModuleCode] = useState("");
  const [ects, setEcts] = useState<string>("");
  const [prof, setProf] = useState("");
  const [language, setLanguage] = useState("");
  const [colorHex, setColorHex] = useState<string>("#7aa5e8");
  const [notes, setNotes] = useState("");
  const [folderName, setFolderName] = useState("");

  const create = useCreateCourse();
  const update = useUpdateCourse();
  const remove = useDeleteCourse();
  const rootFolders = useFilesList("");
  const folderSuggestions = (rootFolders.data ?? [])
    .filter((e) => e.type === "folder")
    .map((e) => e.name);

  useEffect(() => {
    if (!open) return;
    setCode(course?.code ?? "");
    setFullName(course?.full_name ?? "");
    setModuleCode(course?.module_code ?? "");
    setEcts(course?.ects != null ? String(course.ects) : "");
    setProf(course?.prof ?? "");
    setLanguage(course?.language ?? "");
    setColorHex(course?.color_hex || fallbackAccent(course?.code ?? "NEW"));
    setNotes(course?.notes ?? "");
    setFolderName(course?.folder_name ?? "");
  }, [open, course]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmedCode = code.trim().toUpperCase();
    if (!editing && !CODE_PATTERN.test(trimmedCode)) {
      toast.error(t("forms.course.errCodePattern"));
      return;
    }
    if (!fullName.trim()) {
      toast.error(t("forms.course.errNameRequired"));
      return;
    }
    const patch = {
      full_name: fullName.trim(),
      module_code: moduleCode.trim() || undefined,
      ects: ects ? Number(ects) : undefined,
      prof: prof.trim() || undefined,
      language: language.trim() || undefined,
      color_hex: colorHex,
      notes: notes.trim() || undefined,
      folder_name: folderName.trim() || undefined,
    };
    try {
      if (editing && course) {
        await update.mutateAsync({ code: course.code, patch });
        toast.success(t("forms.course.updated", { code: course.code }));
      } else {
        await create.mutateAsync({ code: trimmedCode, ...patch, full_name: fullName.trim() });
        toast.success(t("forms.course.created", { code: trimmedCode }));
      }
      onOpenChange(false);
    } catch (err) {
      toast.error((err as Error).message || t("common.failed"));
    }
  }

  async function onDelete() {
    if (!course) return;
    if (!confirm(t("forms.course.confirmDelete", { code: course.code }))) return;
    try {
      await remove.mutateAsync(course.code);
      toast.success(t("forms.course.deleted", { code: course.code }));
      onOpenChange(false);
    } catch (err) {
      toast.error((err as Error).message || t("common.failed"));
    }
  }

  const busy = create.isPending || update.isPending || remove.isPending;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        title={editing ? t("forms.course.titleEdit", { code: course?.code }) : t("forms.course.titleAdd")}
        description={editing ? undefined : t("forms.course.subtitleAdd")}
      >
        <form onSubmit={onSubmit} className="flex flex-col gap-4">

          {!editing && (
            <Field label={t("forms.course.code")} hint={t("forms.course.codeHint")}>
              <Input
                required
                value={code}
                onChange={(e) => setCode(e.target.value.toUpperCase())}
                maxLength={8}
                placeholder="ASB"
                autoFocus
              />
            </Field>
          )}

          <Field label={t("forms.course.fullName")}>
            <Input
              required
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder={t("forms.course.fullNamePlaceholder")}
            />
          </Field>

          <Field label={t("forms.course.accentColor")}>
            <div className="flex items-center gap-2">
              <input
                type="color"
                value={colorHex}
                onChange={(e) => setColorHex(e.target.value)}
                className="h-9 w-12 rounded cursor-pointer bg-transparent border border-border"
              />
              <Input
                value={colorHex}
                onChange={(e) => setColorHex(e.target.value)}
                className="font-mono"
                placeholder="#7aa5e8"
              />
            </div>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("forms.course.moduleCode")} hint={t("forms.course.moduleCodeHint")}>
              <Input value={moduleCode} onChange={(e) => setModuleCode(e.target.value)} placeholder="INF22" />
            </Field>
            <Field label={t("forms.course.ects")}>
              <Input
                type="number"
                min={0}
                max={30}
                value={ects}
                onChange={(e) => setEcts(e.target.value)}
                placeholder="6"
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("forms.course.professor")}>
              <Input value={prof} onChange={(e) => setProf(e.target.value)} placeholder="Dr. Example" />
            </Field>
            <Field label={t("forms.course.language")}>
              <Input value={language} onChange={(e) => setLanguage(e.target.value)} placeholder="English" />
            </Field>
          </div>

          <Field
            label={t("forms.course.filesFolder")}
            hint={
              folderName.trim()
                ? t("forms.course.filesFolderScope", { name: folderName.trim() })
                : t("forms.course.filesFolderDefault", { code: (code || "?").toUpperCase() })
            }
          >
            <Input
              value={folderName}
              onChange={(e) => setFolderName(e.target.value)}
              placeholder={code ? code.toUpperCase() : ""}
            />
            {folderSuggestions.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                <span className="text-[10.5px] font-mono text-subtle uppercase tracking-[0.06em] mr-1 self-center">
                  {t("forms.course.pickFolder")}
                </span>
                {folderSuggestions.map((name) => (
                  <button
                    key={name}
                    type="button"
                    onClick={() => setFolderName(name)}
                    className={cn(
                      "text-[11px] font-mono px-2 py-0.5 rounded-full border transition-colors",
                      folderName === name
                        ? "border-fg bg-surface-2 text-fg"
                        : "border-border text-muted hover:text-fg hover:border-border-strong"
                    )}
                  >
                    {name}
                  </button>
                ))}
              </div>
            )}
          </Field>

          <Field label={t("forms.course.notes")}>
            <Textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder={t("forms.course.notesPlaceholder")}
            />
          </Field>

          <div className="flex items-center justify-between gap-2 pt-2">
            {editing ? (
              <Button
                type="button"
                variant="ghost"
                onClick={onDelete}
                disabled={busy}
                className="text-critical hover:bg-critical/10"
              >
                <Trash2 className="h-4 w-4 mr-1.5" />
                {t("common.delete")}
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button type="button" variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={busy}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : editing ? t("common.save") : t("common.create")}
              </Button>
            </div>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}
