import { useEffect, useState } from "react";
import { Loader2, ShieldCheck, ShieldOff } from "lucide-react";
import QRCode from "qrcode";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSession, useTotpDisable, useTotpEnable, useTotpSetup } from "@/lib/queries";

type Step = "idle" | "setup" | "confirm" | "disable";

function ProvisioningDisplay({ secret, uri }: { secret: string; uri: string }) {
  const { t } = useTranslation();
  const [qrSvg, setQrSvg] = useState<string>("");

  useEffect(() => {
    QRCode.toString(uri, {
      type: "svg",
      errorCorrectionLevel: "M",
      margin: 1,
      width: 224,
      color: { dark: "#000000", light: "#ffffff" },
    }).then(setQrSvg).catch(() => setQrSvg(""));
  }, [uri]);

  return (
    <div className="flex flex-col gap-3 rounded-md bg-surface-2 border border-border/60 p-3">
      <p className="text-xs text-muted">{t("settings.totp.scanHint")}</p>
      <div className="flex justify-center">
        <div
          className="rounded bg-white p-2"
          style={{ width: 240, height: 240 }}
          dangerouslySetInnerHTML={{ __html: qrSvg }}
        />
      </div>
      <p className="text-xs text-muted mt-1">{t("settings.totp.manualHint")}</p>
      <code className="text-sm font-mono tracking-widest text-center bg-bg/60 border border-border/40 rounded px-2 py-1 select-all break-all">
        {secret}
      </code>
    </div>
  );
}

export function TotpCard() {
  const { t } = useTranslation();
  const session = useSession();
  const setup = useTotpSetup();
  const enable = useTotpEnable();
  const disable = useTotpDisable();

  const [step, setStep] = useState<Step>("idle");
  const [code, setCode] = useState("");
  const [provisioning, setProvisioning] = useState<{ secret: string; uri: string } | null>(null);

  const isEnabled = Boolean(session.data?.totp_enabled);

  async function onStart() {
    setCode("");
    try {
      const r = await setup.mutateAsync();
      setProvisioning({ secret: r.secret, uri: r.provisioning_uri });
      setStep("confirm");
    } catch (e) {
      toast.error((e as Error).message || t("common.failed"));
    }
  }

  async function onConfirm() {
    if (!/^\d{6}$/.test(code)) {
      toast.error(t("settings.totp.codeFormat"));
      return;
    }
    try {
      await enable.mutateAsync(code);
      toast.success(t("settings.totp.enabled"));
      setStep("idle");
      setCode("");
      setProvisioning(null);
    } catch {
      toast.error(t("settings.totp.codeWrong"));
      setCode("");
    }
  }

  async function onDisable() {
    if (!/^\d{6}$/.test(code)) {
      toast.error(t("settings.totp.codeFormat"));
      return;
    }
    try {
      await disable.mutateAsync(code);
      toast.success(t("settings.totp.disabled"));
      setStep("idle");
      setCode("");
    } catch {
      toast.error(t("settings.totp.codeWrong"));
      setCode("");
    }
  }

  if (session.isPending) {
    return <Loader2 className="h-4 w-4 animate-spin text-muted" />;
  }

  // Already enabled — offer disable flow
  if (isEnabled) {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-muted">
          <ShieldCheck className="h-4 w-4 text-ok inline mr-1" />
          {t("settings.totp.statusEnabled")}
        </p>
        {step !== "disable" ? (
          <Button variant="secondary" onClick={() => setStep("disable")}>
            <ShieldOff className="h-4 w-4" /> {t("settings.totp.disableBtn")}
          </Button>
        ) : (
          <div className="flex flex-col gap-2">
            <p className="text-xs text-muted">{t("settings.totp.disableConfirm")}</p>
            <Input
              type="text"
              inputMode="numeric"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              placeholder="123456"
              className="font-mono tracking-[0.3em]"
              autoFocus
            />
            <div className="flex gap-2">
              <Button onClick={onDisable} disabled={disable.isPending} variant="secondary">
                {disable.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : t("settings.totp.disableConfirmBtn")}
              </Button>
              <Button variant="ghost" onClick={() => { setStep("idle"); setCode(""); }}>
                {t("common.cancel")}
              </Button>
            </div>
          </div>
        )}
      </div>
    );
  }

  // Not enabled — offer setup flow
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted">{t("settings.totp.statusDisabled")}</p>
      {step === "idle" && (
        <Button variant="secondary" onClick={onStart} disabled={setup.isPending}>
          {setup.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
          {t("settings.totp.enableBtn")}
        </Button>
      )}
      {step === "confirm" && provisioning && (
        <div className="flex flex-col gap-3">
          <ProvisioningDisplay secret={provisioning.secret} uri={provisioning.uri} />
          <p className="text-xs text-muted">{t("settings.totp.confirmHint")}</p>
          <Input
            type="text"
            inputMode="numeric"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            placeholder="123456"
            className="font-mono tracking-[0.3em]"
            autoFocus
          />
          <div className="flex gap-2">
            <Button onClick={onConfirm} disabled={enable.isPending}>
              {enable.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : t("settings.totp.confirmBtn")}
            </Button>
            <Button variant="ghost" onClick={() => { setStep("idle"); setCode(""); setProvisioning(null); }}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
