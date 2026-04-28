import { useRef, useState, type DragEvent } from 'react';

import { useUploadReceipt } from '@/api/receipts';
import { ApiError } from '@/api/client';

const ACCEPTED_MIMES = 'image/jpeg,image/png,image/webp,image/heic,image/heif,application/pdf';
const MAX_BYTES = 10 * 1024 * 1024; // mirror backend MAX_UPLOAD_BYTES

// Drag-and-drop receipt uploader with a click-to-pick fallback.
//
// We don't pre-validate MIME on the client beyond the ``accept``
// attribute hint — the backend's magic-byte sniff is the real gate.
// The size cap is checked here purely so a hostile file doesn't get
// uploaded and rejected after a multi-megabyte round trip.
export function ReceiptUploader() {
  const fileInput = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [clientError, setClientError] = useState<string | null>(null);
  const upload = useUploadReceipt();

  function submitFiles(files: FileList | null | undefined) {
    if (!files || files.length === 0) return;
    // One file per upload — the backend takes a single ``file`` part
    // per request. Multi-select is a future enhancement; today the
    // first file wins.
    const file = files[0];
    if (!file) return;
    if (file.size > MAX_BYTES) {
      // Pre-flight reject so a multi-megabyte file doesn't traverse
      // the network just to come back with a 413.
      setClientError('That file is over the 10 MB limit.');
      return;
    }
    setClientError(null);
    upload.reset();
    upload.mutate(file);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    submitFiles(event.dataTransfer.files);
  }

  const errorMessage = clientError ?? formatUploadError(upload.error);

  return (
    <div>
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        onClick={() => fileInput.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            fileInput.current?.click();
          }
        }}
        className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
          isDragging
            ? 'border-brand-500 bg-brand-50'
            : 'border-slate-300 bg-white hover:border-brand-400 hover:bg-slate-50'
        }`}
      >
        <p className="text-sm font-medium text-slate-700">
          {upload.isPending ? 'Uploading…' : 'Drop a receipt here, or click to choose a file'}
        </p>
        <p className="text-xs text-slate-500 mt-1">JPEG, PNG, WEBP, HEIC, or PDF · up to 10 MB</p>
        <input
          ref={fileInput}
          type="file"
          accept={ACCEPTED_MIMES}
          className="hidden"
          onChange={(event) => {
            submitFiles(event.target.files);
            // Reset the input value so re-uploading the same file
            // (after a deletion, say) still fires ``onChange``.
            event.target.value = '';
          }}
        />
      </div>
      {errorMessage && (
        <p role="alert" className="mt-2 text-sm text-red-600">
          {errorMessage}
        </p>
      )}
    </div>
  );
}

function formatUploadError(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    if (error.status === 413) return 'That file is over the 10 MB limit.';
    if (error.status === 415) return "We can't read that file type. Try a JPEG, PNG, or PDF.";
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string') return detail;
  }
  return 'Upload failed. Please try again.';
}
