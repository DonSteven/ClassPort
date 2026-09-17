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
import type { CourseCode, Slot, SlotKind } from "@/data/types";
import {
  useCreateSlot,
  useDeleteSlot,
  useUpdateSlot,
} from "@/lib/queries";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  slot?: Slot | null;
  courseCode: CourseCode;
};

const WEEKDAY_IDS: { value: 1 | 2 | 3 | 4 | 5 | 6 | 7; key: string }[] = [
  { value: 1, key: "weekdays.monShort" },
  { value: 2, key: "weekdays.tueShort" },
  { value: 3, key: "weekdays.wedShort" },
  { value: 4, key: "weekdays.thuShort" },
  { value: 5, key: "weekdays.friShort" },
  { value: 6, key: "weekdays.satShort" },
  { value: 7, key: "weekdays.sunShort" },
];

const KINDS: SlotKind[] = ["lecture", "exercise", "tutorial", "lab"];

function toTimeStr(t: string | undefined): string {
  if (!t) return "";
  // "HH:MM:SS" or "HH:MM" → "HH:MM"
  return t.slice(0, 5);
}

export function SlotForm({ open, onOpenChange, slot, courseCode }: Props) {
  const { t } = useTranslation();
  const editing = Boolean(slot);
  const [weekday, setWeekday] = useState<1 | 2 | 3 | 4 | 5 | 6 | 7>(1);
  const [kind, setKind] = useState<SlotKind>("lecture");
  const [startTime, setStartTime] = useState("10:00");
  const [endTime, setEndTime] = useState("11:30");
  const [room, setRoom] = useState("");
  const [person, setPerson] = useState("");
  const [startsOn, setStartsOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [notes, setNotes] = useState("");

  const create = useCreateSlot();
  const update = useUpdateSlot();
  const remove = useDeleteSlot();

  useEffect(() => {
    if (!open) return;
    setWeekday(slot?.weekday ?? 1);
    setKind((slot?.kind as SlotKind) ?? "lecture");
    setStartTime(toTimeStr(slot?.start_time) || "10:00");
    setEndTime(toTimeStr(slot?.end_time) || "11:30");
    setRoom(slot?.room ?? "");
    setPerson(slot?.person ?? "");
    setStartsOn(slot?.starts_on ?? "");
    setEndsOn(slot?.ends_on ?? "");
    setNotes(slot?.notes ?? "");
  }, [open, slot]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!startTime || !endTime) {
      toast.error(t("forms.slot.errTimesRequired"));
      return;
    }
    if (startTime >= endTime) {
      toast.error(t("forms.slot.errEndAfterStart"));
      return;
    }
    const payload = {
      course_code: courseCode,
      kind,
      weekday,
      start_time: startTime,
      end_time: endTime,
      room: room.trim() || undefined,
      person: person.trim() || undefined,
      starts_on: startsOn || undefined,
      ends_on: endsOn || undefined,
      notes: notes.trim() || undefined,
    };
    try {
      if (editing && slot) {
        await update.mutateAsync({ id: slot.id, patch: payload });
        toast.success(t("forms.slot.updated"));
      } else {
        await create.mutateAsync(payload);
        toast.success(t("forms.slot.created"));
      }
      onOpenChange(false);
    } catch (err) {
      toast.error((err as Error).message || t("common.failed"));
    }
  }

  async function onDelete() {
    if (!slot) return;
    if (!window.confirm(t("forms.slot.confirmDelete"))) return;
    try {
      await remove.mutateAsync(slot.id);
      toast.success(t("forms.slot.deleted"));
      onOpenChange(false);
    } catch (err) {
      toast.error((err as Error).message || t("common.failed"));
    }
  }

  const pending = create.isPending || update.isPending || remove.isPending;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent title={editing ? t("forms.slot.titleEdit") : t("forms.slot.titleAdd")}>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label={t("forms.slot.weekday")}>
              <Select
                value={String(weekday)}
                onValueChange={(v) =>
                  setWeekday(Number(v) as 1 | 2 | 3 | 4 | 5 | 6 | 7)
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder={t("forms.slot.weekday")} />
                </SelectTrigger>
                <SelectContent>
                  {WEEKDAY_IDS.map((d) => (
                    <SelectItem key={d.value} value={String(d.value)}>
                      {t(d.key)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label={t("forms.slot.kind")}>
              <Select value={kind} onValueChange={(v) => setKind(v as SlotKind)}>
                <SelectTrigger>
                  <SelectValue placeholder={t("forms.slot.kind")} />
                </SelectTrigger>
                <SelectContent>
                  {KINDS.map((k) => (
                    <SelectItem key={k} value={k}>
                      {t(`kinds.slot.${k}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label={t("forms.slot.startTime")}>
              <Input
                type="time"
                required
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
              />
            </Field>
            <Field label={t("forms.slot.endTime")}>
              <Input
                type="time"
                required
                value={endTime}
                onChange={(e) => setEndTime(e.target.value)}
              />
            </Field>
          </div>

          <Field label={t("forms.slot.room")} hint={t("forms.slot.roomHint")}>
            <Input
              value={room}
              onChange={(e) => setRoom(e.target.value)}
              placeholder={t("forms.slot.roomPlaceholder")}
            />
          </Field>

          <Field label={t("forms.slot.person")} hint={t("forms.slot.personHint")}>
            <Input
              value={person}
              onChange={(e) => setPerson(e.target.value)}
              placeholder={t("forms.slot.personPlaceholder")}
            />
          </Field>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label={t("forms.slot.startsOn")} hint={t("forms.slot.startsOnHint")}>
              <Input
                type="date"
                value={startsOn}
                onChange={(e) => setStartsOn(e.target.value)}
              />
            </Field>
            <Field label={t("forms.slot.endsOn")}>
              <Input
                type="date"
                value={endsOn}
                onChange={(e) => setEndsOn(e.target.value)}
              />
            </Field>
          </div>

          <Field label={t("forms.slot.notes")}>
            <Textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              placeholder={t("forms.slot.notesPlaceholder")}
            />
          </Field>

          <div className="flex items-center justify-between gap-2 pt-2">
            {editing ? (
              <Button
                type="button"
                variant="ghost"
                onClick={onDelete}
                disabled={pending}
                className="text-critical hover:bg-critical/10"
              >
                <Trash2 className="h-4 w-4 mr-1.5" />
                {t("common.delete")}
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
                disabled={pending}
              >
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : editing ? t("common.save") : t("common.create")}
              </Button>
            </div>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}
