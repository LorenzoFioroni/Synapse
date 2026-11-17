export interface GraphNode {
  id: string;
  kind: 'paper' | 'work' | 'note' | 'pdf';
  title: string;
  authors?: string;
  year?: number | string | null;
  doi?: string | null;
  arxiv_id?: string | null;
  item_id?: string;        // papers
  citation_key?: string;   // papers
  item_type?: string;
  folder_ids?: number[];
  tags?: string[];
  has_pdf?: boolean;
  path?: string;           // notes
  note_type?: string | null;
  paper?: string | null;
  pdf_id?: number;         // PDFs in the notes folders
}

/** cites: paper → paper/work (reference lists); cite: note → paper; link: note → note; about: paper note → paper; pdf: note → a PDF in the notes folders. */
export type EdgeKind = 'cites' | 'cite' | 'link' | 'about' | 'pdf';

export interface GraphEdge { source: string; target: string; kind: EdgeKind }

export interface GraphData { nodes: GraphNode[]; edges: GraphEdge[] }
