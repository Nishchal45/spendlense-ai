import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { apiFetch } from './client';

// Wire types mirror ``backend/app/schemas/receipt.py``. ``parsed_payload``
// is the JSONB blob the OCR pipeline writes — we mirror its shape from
// ``services/receipt_parser.py``.

export const RECEIPT_STATUSES = [
  'uploaded',
  'processing',
  'parsed',
  'categorised',
  'failed',
] as const;

export type ReceiptStatus = (typeof RECEIPT_STATUSES)[number];

export type OcrMethod = 'tesseract' | 'gpt4v';

export interface ParsedPayload {
  merchant: string | null;
  total: string | null;
  transaction_date: string | null;
  line_items: unknown[];
}

export interface Receipt {
  id: string;
  user_id: string;
  mime_type: string;
  file_size_bytes: number;
  status: ReceiptStatus;
  ocr_method: OcrMethod | null;
  ocr_confidence: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReceiptStatusOut extends Omit<
  Receipt,
  'user_id' | 'mime_type' | 'file_size_bytes'
> {
  error_message: string | null;
  parsed_payload: ParsedPayload | null;
}

export interface ReceiptsList {
  items: Receipt[];
}

// ``in-flight`` rows are still moving through the pipeline; the status
// page polls until each lands in a terminal state. Putting this set in
// one place keeps poll cadence + status-badge styling in agreement.
export const TERMINAL_STATUSES: ReadonlySet<ReceiptStatus> = new Set(['categorised', 'failed']);

// ----- queries -------------------------------------------------------------

export function useReceipts() {
  return useQuery<ReceiptsList>({
    queryKey: ['receipts'],
    queryFn: () => apiFetch<ReceiptsList>('/receipts'),
    // The list is small (capped at 50 server-side) and changes when
    // we upload, retry, or delete. Refresh it every 5 s so a row
    // moving through ``processing → parsed → categorised`` updates
    // without us re-architecting around per-row polling.
    refetchInterval: 5_000,
  });
}

/**
 * Per-row status with the parsed payload + error message.
 *
 * Polled aggressively (every 2 s) while the row is in flight, then
 * stops. ``enabled`` lets a card decide when to subscribe — a
 * categorised receipt doesn't need polling at all.
 */
export function useReceiptStatus(receiptId: string, enabled: boolean) {
  return useQuery<ReceiptStatusOut>({
    queryKey: ['receipts', receiptId, 'status'],
    queryFn: () => apiFetch<ReceiptStatusOut>(`/receipts/${receiptId}/status`),
    enabled,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status && TERMINAL_STATUSES.has(status)) return false;
      return 2_000;
    },
  });
}

// ----- mutations -----------------------------------------------------------

export function useUploadReceipt() {
  const queryClient = useQueryClient();
  return useMutation<Receipt, Error, File>({
    mutationFn: async (file) => {
      const body = new FormData();
      body.append('file', file);
      // ``apiFetch`` would normally set ``Content-Type: application/json``
      // when a body is present. ``FormData`` needs the browser to pick
      // its own ``multipart/form-data; boundary=...`` — passing
      // ``Content-Type: undefined`` via the headers won't work, so we
      // pre-create the Headers object without it. The auth bearer is
      // attached as usual inside ``apiFetch``.
      return apiFetch<Receipt>('/receipts', {
        method: 'POST',
        body,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['receipts'] });
    },
  });
}

export function useDeleteReceipt() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => apiFetch<void>(`/receipts/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['receipts'] });
    },
  });
}

export function useRetryReceipt() {
  const queryClient = useQueryClient();
  return useMutation<Receipt, Error, string>({
    mutationFn: (id) => apiFetch<Receipt>(`/receipts/${id}/retry`, { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['receipts'] });
    },
  });
}

// ----- view helpers --------------------------------------------------------

const STATUS_LABELS: Record<ReceiptStatus, string> = {
  uploaded: 'Queued',
  processing: 'Processing',
  parsed: 'Read',
  categorised: 'Categorised',
  failed: 'Failed',
};

export function statusLabel(status: ReceiptStatus): string {
  return STATUS_LABELS[status];
}

export function isInFlight(status: ReceiptStatus): boolean {
  return !TERMINAL_STATUSES.has(status);
}
