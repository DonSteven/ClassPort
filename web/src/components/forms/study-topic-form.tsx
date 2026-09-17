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
import type { CourseCode, Lecture, StudyTopic, StudyTopicKind, StudyTopicStatus } from "@/data/types";
import { useCreateStudyTopic, useDeleteStudyTopic, useUpdateStudyTopic } from "@/lib/queries";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  topic?: StudyTopic | null;
  courseCode: CourseCode;
  lectures: Lecture[];
};

export function StudyTopicForm({ open, onOpenChange, topic, courseCode, lectures }: Props) {
  const { t } = useTranslation();
  const editing = Boolean(topic);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [chapter, setChapter] = useState("");
  const [kind, setKind] = useState<StudyTopicKind | "">("");
  const [coveredOn, setCoveredOn] = useState("");
  const [lectureId, setLectureId] = useState<string>("");
  const [status, setStatus] = useState<StudyTopicStatus>("not_started");
  const [confidence, setConfidence] = useState<string>("");
  const [notes, setNotes] = useState("");

  const create = useCreateStudyTopic();
  const update = useUpdateStudyTopic();
  const del = useDeleteStudyTopic();

  useEffect(() => {
    if (open) {
      setName(topic?.name ?? "");
      setDescription(topic?.description ?? "");
      setChapter(topic?.chapter ?? "");
      setKind((topic?.kind as StudyTopicKind) ?? "");
      setCoveredOn(topic?.covered_on ?? "");
      setLectureId(topic?.lecture_id ?? "");
      setStatus(topic?.status ?? "not_started");
      setConfidence(topic?.confidence != null ? String(topic.confidence) : "");
      setNotes(topic?.notes ?? "");
    }
  }, [open, topic]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const payload: Record<string, unknown> = {
      course_code: courseCode,
      name: name.trim(),
      description: description.trim() || undefined,
      chapter: chapter.trim() || undefined,
      kind: kind || undefined,
      covered_on: coveredOn || undefined,
      lecture_id: lectureId || undefined,
      status,
      notes: notes.trim() || undefined,
    };
    if (confidence) payload.confidence = Number(confidence);
    try {
      if (editing && topic) {
        await update.mutateAsync({ id: topic.id, patch: payload });
        toast.success(t("forms.studyTopic.updated"));
      } else {
        await create.mutateAsync(payload as never);
        toast.success(t("forms.studyTopic.created"));
      }
      onOpenChange(false);
    } catch (e) {
      toast.error((e as Error).message || t("common.failed"));
    }
  }

  async function onDelete() {
    if (!topic) return;
    if (!confirm(t("forms.studyTopic.confirmDelete"))) return;
    try {
      await del.mutateAsync(topic.id);
      toast.success(t("forms.studyTopic.deleted"));
      onOpenChange(false);
    } catch (e) {
      toast.error((e as Error).message || t("common.failed"));
    }
  }

  const pending = create.isPending || update.isPending;
  const courseLectures = lectures.filter((l) => l.course_code === courseCode);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent title={editing ? t("forms.studyTopic.titleEdit") : t("forms.studyTopic.titleAdd")}>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <Field label={t("forms.studyTopic.name")}>
            <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus required />
          </Field>

          <Field label={t("forms.studyTopic.description")}>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={8}
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("forms.studyTopic.chapter")}>
              <Input value={chapter} onChange={(e) => setChapter(e.target.value)} />
            </Field>
            <Field label={t("forms.studyTopic.kind")}>
              <Select value={kind || "__none__"} onValueChange={(v) => setKind(v === "__none__" ? "" : (v as StudyTopicKind))}>
                <SelectTrigger>
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">—</SelectItem>
                  <SelectItem value="lecture">{t("kinds.slot.lecture")}</SelectItem>
                  <SelectItem value="exercise">{t("kinds.slot.exercise")}</SelectItem>
                  <SelectItem value="reading">{t("forms.studyTopic.kindReading", "Reading")}</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("forms.studyTopic.coveredOn", "Covered on")}>
              <Input
                type="date"
                value={coveredOn}
                onChange={(e) => setCoveredOn(e.target.value)}
              />
            </Field>
            <Field label={t("forms.studyTopic.status")}>
              <Select value={status} onValueChange={(v) => setStatus(v as StudyTopicStatus)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="not_started">{t("kinds.topicStatus.not_started", "Not started")}</SelectItem>
                  <SelectItem value="in_progress">{t("kinds.status.in_progress")}</SelectItem>
                  <SelectItem value="studied">{t("kinds.topicStatus.studied", "Studied")}</SelectItem>
                  <SelectItem value="mastered">{t("kinds.topicStatus.mastered", "Mastered")}</SelectItem>
                  <SelectItem value="struggling">{t("kinds.topicStatus.struggling", "Struggling")}</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>

          <Field label={t("forms.studyTopic.lecture")}>
            <Select
              value={lectureId || "__none__"}
              onValueChange={(v) => setLectureId(v === "__none__" ? "" : v)}
            >
              <SelectTrigger>
                <SelectValue placeholder="—" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">— {t("forms.studyTopic.lectureNone").toLowerCase()} —</SelectItem>
                {courseLectures.map((l) => (
                  <SelectItem key={l.id} value={l.id}>
                    #{l.number ?? "?"} · {l.held_on ?? "—"}
                    {l.title ? ` · ${l.title}` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("forms.studyTopic.confidence")}>
              <Input
                type="number"
                min="0"
                max="5"
                value={confidence}
                onChange={(e) => setConfidence(e.target.value)}
              />
            </Field>
            <div />
          </div>

          <Field label={t("forms.studyTopic.notes")}>
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
              <Button type="submit" disabled={pending || !name.trim()}>
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
