import { fmtEastern, fromEasternInput } from "@/lib/time";
import { useEffect, useState, type FormEvent } from "react";
import { Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input, Textarea, Field } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  Course,
  CourseCode,
  Deliverable,
  DeliverableKind,
  DeliverableStatus,
} from "@/data/types";
import {
  useCreateDeliverable,
  useDeleteDeliverable,
  useUpdateDeliverable,
} from "@/lib/queries";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  deliverable?: Deliverable | null;
  defaultCourse?: CourseCode;
  courses: Course[];
};

function toLocalInput(iso: string | undefined): string {
  if (!iso) return "";
  return fmtEastern(iso, "yyyy-MM-dd'T'HH:mm");
}
function fromLocalInput(local: string): string | undefined {
  if (!local) return undefined;
  return fromEasternInput(local);
}

export function DeliverableForm({
  open,
  onOpenChange,
  deliverable,
  defaultCourse,
  courses,
}: Props) {
  const { t } = useTranslation();
  const editing = Boolean(deliverable);
  const [name, setName] = useState("");
  const [courseCode, setCourseCode] = useState<string>("");
  const [kind, setKind] = useState<DeliverableKind | "">("");
  const [availableAt, setAvailableAt] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [status, setStatus] = useState<DeliverableStatus>("open");
  const [localPath, setLocalPath] = useState("");
  const [externalUrl, setExternalUrl] = useState("");
  const [weightInfo, setWeightInfo] = useState("");
  const [notes, setNotes] = useState("");

  const create = useCreateDeliverable();
  const update = useUpdateDeliverable();
  const del = useDeleteDeliverable();

  useEffect(() => {
    if (open) {
      setName(deliverable?.name ?? "");
      setCourseCode(deliverable?.course_code ?? defaultCourse ?? "");
      setKind((deliverable?.kind as DeliverableKind) ?? "");
      setAvailableAt(toLocalInput(deliverable?.available_at));
      setDueAt(toLocalInput(deliverable?.due_at));
      setStatus(deliverable?.status ?? "open");
      setLocalPath(deliverable?.local_path ?? "");
      setExternalUrl(deliverable?.external_url ?? "");
      setWeightInfo(deliverable?.weight_info ?? "");
      setNotes(deliverable?.notes ?? "");
    }
  }, [open, deliverable, defaultCourse]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim() || !courseCode || !dueAt) return;
    const payload = {
      course_code: courseCode as CourseCode,
      name: name.trim(),
      kind: (kind || undefined) as DeliverableKind | undefined,
      available_at: fromLocalInput(availableAt),
      due_at: fromLocalInput(dueAt)!,
      status,
      local_path: localPath.trim() || undefined,
      external_url: externalUrl.trim() || undefined,
      weight_info: weightInfo.trim() || undefined,
      notes: notes.trim() || undefined,
    };
    try {
      if (editing && deliverable) {
        await update.mutateAsync({ id: deliverable.id, patch: payload });
        toast.success(t("forms.deliverable.updated"));
      } else {
        await create.mutateAsync(payload);
        toast.success(t("forms.deliverable.created"));
      }
      onOpenChange(false);
    } catch (e) {
      toast.error((e as Error).message || t("common.failed"));
    }
  }

  async function onDelete() {
    if (!deliverable) return;
    if (!confirm(t("forms.deliverable.confirmDelete"))) return;
    try {
      await del.mutateAsync(deliverable.id);
      toast.success(t("forms.deliverable.deleted"));
      onOpenChange(false);
    } catch (e) {
      toast.error((e as Error).message || t("common.failed"));
    }
  }

  const pending = create.isPending || update.isPending;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent title={editing ? t("forms.deliverable.titleEdit") : t("forms.deliverable.titleAdd")}>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <Field label={t("forms.deliverable.name")}>
            <Input value={name} onChange={(e) => setName(e.target.value)} required />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label={t("forms.deliverable.course")}>
              <Select value={courseCode} onValueChange={setCourseCode}>
                <SelectTrigger>
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  {courses.map((c) => (
                    <SelectItem key={c.code} value={c.code}>
                      {c.code}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label={t("forms.deliverable.kind")}>
              <Select value={kind || "__none__"} onValueChange={(v) => setKind(v === "__none__" ? "" : (v as DeliverableKind))}>
                <SelectTrigger>
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">—</SelectItem>
                  <SelectItem value="submission">{t("forms.deliverable.kindSubmission")}</SelectItem>
                  <SelectItem value="project">{t("forms.deliverable.kindProject")}</SelectItem>
                  <SelectItem value="lab">{t("forms.deliverable.kindLab")}</SelectItem>
                  <SelectItem value="block">{t("forms.deliverable.kindBlock")}</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label={t("forms.deliverable.submittedAt")}>
              <Input
                type="datetime-local"
                value={availableAt}
                onChange={(e) => setAvailableAt(e.target.value)}
              />
            </Field>
            <Field label={t("forms.deliverable.dueAt")}>
              <Input
                type="datetime-local"
                required
                value={dueAt}
                onChange={(e) => setDueAt(e.target.value)}
              />
            </Field>
          </div>
          <Field label={t("forms.deliverable.status")}>
            <Select value={status} onValueChange={(v) => setStatus(v as DeliverableStatus)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="open">{t("kinds.status.open")}</SelectItem>
                <SelectItem value="in_progress">{t("kinds.status.in_progress")}</SelectItem>
                <SelectItem value="submitted">{t("kinds.status.submitted")}</SelectItem>
                <SelectItem value="graded">{t("kinds.status.done", "Graded")}</SelectItem>
                <SelectItem value="skipped">{t("kinds.status.skipped")}</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label={t("forms.deliverable.localPath", "Local path")}>
            <Input value={localPath} onChange={(e) => setLocalPath(e.target.value)} />
          </Field>
          <Field label={t("forms.deliverable.externalUrl", "External URL")}>
            <Input value={externalUrl} onChange={(e) => setExternalUrl(e.target.value)} />
          </Field>
          <Field label={t("forms.deliverable.weight")}>
            <Input value={weightInfo} onChange={(e) => setWeightInfo(e.target.value)} />
          </Field>
          <Field label={t("forms.deliverable.notes")}>
            <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>

          <div className="flex items-center justify-between gap-2 pt-2">
            {editing ? (
              <Button type="button" variant="danger" size="md" onClick={onDelete}>
                <Trash2 className="h-4 w-4" /> {t("common.delete")}
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={pending || !name.trim() || !courseCode || !dueAt}>
                {pending && <Loader2 className="h-4 w-4 animate-spin" />}
                {editing ? t("common.save") : t("common.create")}
              </Button>
            </div>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}
