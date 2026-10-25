import { api } from "./api";

export type FileUploadTarget = {
  file_id: string;
  version_id: string;
  object_version?: string;
};

export async function uploadFile(file: File, path: string): Promise<FileUploadTarget> {
  const bytes = await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(new Error("Could not read upload file"));
    reader.readAsArrayBuffer(file);
  });
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const sha256 = Array.from(new Uint8Array(digest), (n) => n.toString(16).padStart(2, "0")).join("");
  const { url, headers, file_id, version_id, storage_backend } = await api.post<{
    url: string; headers: Record<string, string>; file_id: string; version_id: string;
    storage_backend: "local" | "s3";
  }>("/api/files/upload-url", {
    path, size: file.size, sha256, content_type: file.type || "application/octet-stream",
  });
  const response = await fetch(url, {
    method: "PUT", body: file,
    credentials: new URL(url, location.href).origin === location.origin ? "same-origin" : "omit",
    headers: { "Content-Type": file.type || "application/octet-stream", ...headers },
  });
  if (!response.ok) throw new Error(`upload failed: ${response.status} ${response.statusText}`);
  const object_version = response.headers.get("x-amz-version-id") ?? undefined;
  if (storage_backend === "s3" && (!object_version || object_version === "null")) {
    throw new Error("Upload did not return an object version; check bucket versioning and CORS");
  }
  return { file_id, version_id, object_version };
}
