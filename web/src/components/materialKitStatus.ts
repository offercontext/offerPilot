import type { EditableMaterialKitStatus, MaterialKitStatus } from '@/types/materialKit';

export function getMaterialKitStatusForSave(
  persistedStatus: MaterialKitStatus,
  selectedStatus: EditableMaterialKitStatus,
): EditableMaterialKitStatus | undefined {
  return persistedStatus === 'submitted' ? undefined : selectedStatus;
}
