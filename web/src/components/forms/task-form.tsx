import { fmtEastern, fromEasternInput } from "@/lib/time";
import { useEffect, useState, type FormEvent } from "react";
import { Loader2, Trash2 } from "lucide-react";
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
import type { Course, Task, TaskPriority } from "@/data/types";
import { useCreateTask, useDeleteTask, useUpdateTask } from "@/lib/queries";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  task?: Task | null; // if present → edit; else → create
  defaultCourse?: string;
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

export function TaskForm({ open, onOpenChange, task, defaultCourse, courses }: Props) {
  const { t } = useTranslation();
  const editing = Boolean(task);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [courseCode, setCourseCode] = useState<string>("");
  const [dueAt, setDueAt] = useState<string>("");
  const [priority, setPriority] = useState<TaskPriority>("med");
  const [tags, setTags] = useState("");

  const createTask = useCreateTask();
  const updateTask = useUpdateTask();
  const deleteTask = useDeleteTask();

  useEffect(() => {
    if (open) {
      setTitle(task?.title ?? "");
      setDescription(task?.description ?? "");
      setCourseCode(task?.course_code ?? defaultCourse ?? "");
      setDueAt(toLocalInput(task?.due_at));
      setPriority(task?.priority ?? "med");
      setTags((task?.tags ?? []).join(", "));
    }
  }, [open, task, defaultCourse]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    const payload = {
      title: title.trim(),
      description: description.trim() || undefined,
      course_code: courseCode ? (courseCode as Task["course_code"]) : undefined,
      due_at: fromLocalInput(dueAt),
      priority,
      tags: tags.split(",").map((t) => t.trim()).filter(Boolean) || undefined,
    };
    try {
      if (editing && task) {
        await updateTask.mutateAsync({ id: task.id, patch: payload });
        toast.success(t("forms.task.updated"));
      } else {
        await createTask.mutateAsync(payload);
        toast.success(t("forms.task.created"));
      }
      onOpenChange(false);
    } catch (e) {
      toast.error((e as Error).message || t("common.failed"));
    }
  }

  async function onDelete() {
    if (!task) return;
    if (!confirm(t("forms.task.confirmDelete"))) return;
    try {
      await deleteTask.mutateAsync(task.id);
      toast.success(t("forms.task.deleted"));
      onOpenChange(false);
    } catch (e) {
      toast.error((e as Error).message || t("common.failed"));
    }
  }

  const pending = createTask.isPending || updateTask.isPending;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent title={editing ? t("forms.task.titleEdit") : t("forms.task.titleAdd")}>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <Field label={t("forms.task.titleField")}>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} required />
          </Field>

          <Field label={t("forms.task.description")}>
            <Textarea value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("forms.task.course")}>
              <Select value={courseCode || "__none__"} onValueChange={(v) => setCourseCode(v === "__none__" ? "" : v)}>
                <SelectTrigger>
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">{t("forms.task.courseNone")}</SelectItem>
                  {courses.map((c) => (
                    <SelectItem key={c.code} value={c.code}>
                      {c.code} · {c.short_name ?? c.full_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label={t("forms.task.priority")}>
              <Select value={priority} onValueChange={(v) => setPriority(v as TaskPriority)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="low">{t("forms.task.priorityLow")}</SelectItem>
                  <SelectItem value="med">{t("forms.task.priorityMed")}</SelectItem>
                  <SelectItem value="high">{t("forms.task.priorityHigh")}</SelectItem>
                  <SelectItem value="urgent">{t("forms.task.priorityUrgent")}</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>

          <Field label={t("forms.task.due")} hint={t("forms.task.dueHint")}>
            <Input
              type="datetime-local"
              value={dueAt}
              onChange={(e) => setDueAt(e.target.value)}
            />
          </Field>

          <Field label={t("forms.task.tags")}>
            <Input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder={t("forms.task.tagsPlaceholder")}
            />
          </Field>

          <div className="flex items-center justify-between gap-2 pt-2">
            {editing ? (
              <Button type="button" variant="danger" size="md" onClick={onDelete}>
                <Trash2 className="h-4 w-4" />
                {t("common.delete")}
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={pending || !title.trim()}>
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
