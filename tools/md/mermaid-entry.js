// Mermaid as one classic script that sets window.mermaid (loaded on demand by richRender.ts).
// Built here instead of copying Mermaid's own mermaid.min.js, which carries the copies of its
// dependencies (DOMPurify among them) from when Mermaid was released: this way the versions in
// package-lock.json are the ones that run, and a security update needs only `npm audit fix`.
import mermaid from 'mermaid';

window.mermaid = mermaid;
