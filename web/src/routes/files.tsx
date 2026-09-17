import { useTranslation } from "react-i18next";
import { Header } from "@/components/layout/header";
import { FileBrowser } from "@/components/files/file-browser";

export default function Files() {
  const { t } = useTranslation();
  return (
    <>
      <Header title={t("files.title")} subtitle={t("files.subtitle")} />
      <div className="px-4 md:px-8 py-4 md:py-6 max-w-[1000px] mx-auto w-full">
        <FileBrowser />
      </div>
    </>
  );
}
