// Plugin-owned note exports; registrations disappear with their owning plugin.
import type { NoteExportSpec } from '../../plugins/api.js';
export const noteExports = new Map<string, NoteExportSpec>();
