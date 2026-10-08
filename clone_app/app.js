
/* ---------------------------------------------------------------- word diff */
function tokens(s) { return String(s ?? "").match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu) || []; }
function diffWords(a, b) {
  const A = tokens(a), B = tokens(b);
  if (A.length * B.length > 250000) return {old: `<del>${escapeHtml(a)}</del>`, new: `<ins>${escapeHtml(b)}</ins>`};
  const n = A.length, m = B.length, dp = Array.from({length: n + 1}, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let i = 0, j = 0, o = "", nw = "";
  const flush = (arr, tag) => arr.length ? `<${tag}>${escapeHtml(arr.join(""))}</${tag}>` : "";
  let da = [], ib = [];
  while (i < n || j < m) {
    if (i < n && j < m && A[i] === B[j]) { o += flush(da, "del"); nw += flush(ib, "ins"); da = []; ib = []; o += escapeHtml(A[i]); nw += escapeHtml(B[j]); i++; j++; }
    else if (j < m && (i >= n || dp[i][j + 1] >= dp[i + 1][j])) { ib.push(B[j++]); }
    else { da.push(A[i++]); }
  }
  o += flush(da, "del"); nw += flush(ib, "ins");
  return {old: o, new: nw};
}
function escapeHtml(unsafe) {
    return (unsafe || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}




let searchTimeout;
let currentPapers = [];

// Author resolution and deduplication helpers
function removeAccents(str) {
    if (!str) return "";
    return str.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z]/g, '');
}

function isGivenNameCompatible(fetched, known) {
    let fParts = (fetched || "").split(/[\s\.\-]+/).filter(x => x.trim().length > 0);
    let kParts = (known || "").split(/[\s\.\-]+/).filter(x => x.trim().length > 0);
    
    // MUST have the exact same number of name parts (no expanding initials to full names!)
    if (fParts.length === 0 || fParts.length !== kParts.length) return false;
    
    for (let i = 0; i < fParts.length; i++) {
        let fp = removeAccents(fParts[i]);
        let kp = removeAccents(kParts[i]);
        // MUST be an exact match (ignoring accents/case). "a" does not equal "adam"
        if (fp !== kp) {
            return false;
        }
    }
    return true;
}


async function fetchOpenAlexAuthors(doi, allAuthors) {
    try {
        let clean = doi.replace(/^https?:\/\/(dx\.)?doi\.org\//i, '').replace(/^doi:/i, '').trim();
        let res = await fetch(`https://api.openalex.org/works/doi:${encodeURIComponent(clean)}`);
        if (!res.ok) return null;
        let data = await res.json();
        if (data.authorships && data.authorships.length > 0) {
            return data.authorships.map(a => {
                let name = a.author.display_name || '';
                let parts = name.trim().split(/\s+/);
                let fam = parts.length > 1 ? parts.pop() : name;
                let giv = parts.length > 0 ? parts.join(' ') : '';
                return resolveAuthor(fam, giv, allAuthors);
            }).join('; ');
        }
    } catch(e) {}
    return null;
}

function getAllKnownAuthors() {
    let allKnownAuthors = [];
    if (typeof currentPapers !== 'undefined' && currentPapers) {
        currentPapers.forEach(p => {
            if (p.authors) {
                p.authors.split(';').forEach(a => {
                    let parts = a.split(',');
                    if (parts.length === 2) {
                        allKnownAuthors.push({
                            family: parts[0].trim(),
                            given: parts[1].trim(),
                            original: a.trim()
                        });
                    }
                });
            }
        });
    }
    return allKnownAuthors;
}

function resolveAuthor(fetchedFamily, fetchedGiven, allKnownAuthors) {
    let cleanFetchFam = removeAccents(fetchedFamily);
    if (!cleanFetchFam) return `${fetchedFamily || ''}, ${fetchedGiven || ''}`.trim().replace(/^,|,$/g, '').trim();
    
    let matches = allKnownAuthors.filter(k => {
        if (removeAccents(k.family) !== cleanFetchFam) return false;
        return isGivenNameCompatible(fetchedGiven, k.given);
    });
    
    if (matches.length > 0) {
        let uniqueExpansions = {};
        matches.forEach(m => {
            let key = removeAccents(m.family) + "|" + removeAccents(m.given);
            if (!uniqueExpansions[key]) {
                uniqueExpansions[key] = m.original;
            } else {
                if (m.original.length > uniqueExpansions[key].length) {
                    uniqueExpansions[key] = m.original;
                }
            }
        });
        
        let keys = Object.keys(uniqueExpansions);
        if (keys.length === 1) {
            let bestMatch = uniqueExpansions[keys[0]];
            let bestLen = bestMatch.length;
            let fetchStr = `${fetchedFamily}, ${fetchedGiven}`;
            let fetchLen = fetchStr.length;
            
            let hasAccents = /[^\u0000-\u007F]/.test(bestMatch);
            let fetchHasAccents = /[^\u0000-\u007F]/.test(fetchStr);
            
            // We only prefer the local match if it has proper accents (and the fetched doesn't)
            // or if it has superior casing. We do NOT substitute if lengths are different to avoid initial-collisions.
            if (hasAccents && !fetchHasAccents) {
                return bestMatch;
            }
        }
    }
    return `${fetchedFamily || ''}, ${fetchedGiven || ''}`.trim().replace(/^,|,$/g, '').trim();
}

let contextPaperId = null;
let contextAttId = null;


let currentSortCol = 'id';
let currentSortDir = 'desc';


let columnOrder = [];

function applyColumnOrderStyle() {
    let styleEl = document.getElementById('dynamic-column-order');
    if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = 'dynamic-column-order';
        document.head.appendChild(styleEl);
    }
    let css = '';
    columnOrder.forEach((colClass, index) => {
        css += `.${colClass} { order: ${index}; }\n`;
    });
    styleEl.textContent = css;
}

let columnVisibility = {
    'col-attachments': true,
    'col-title': true,
    'col-creator': true,
    'col-type': true,
    'col-year': true,
    'col-journal': false,
    'col-added': true,
    'col-citation_key': false,
    'col-pmid': false, 'col-pmcid': false, 'col-issn': false, 'col-isbn': false,
    'col-doi': false, 'col-url': false, 'col-archive': false, 'col-archive_id': false,
    'col-short_title': false, 'col-language': false, 'col-library_catalog': false,
    'col-call_number': false, 'col-license': false, 'col-extra': false,
    'col-series': false, 'col-edition': false, 'col-place': false,
    'col-publisher': false, 'col-volume': false, 'col-issue': false, 'col-pages': false
};

function applySorting() {
    if (!currentPapers) return;
    currentPapers.sort((a, b) => {
        let valA = a[currentSortCol] || '';
        let valB = b[currentSortCol] || '';
        
        // Number comparison for year
        if (currentSortCol === 'year') {
            valA = parseInt(valA) || 0;
            valB = parseInt(valB) || 0;
        } else if (typeof valA === 'string') {
            valA = valA.toLowerCase();
            valB = valB.toLowerCase();
        }
        
        if (valA < valB) return currentSortDir === 'asc' ? -1 : 1;
        if (valA > valB) return currentSortDir === 'asc' ? 1 : -1;
        return 0;
    });
    
    // Update UI headers
    document.querySelectorAll('.list-header-col').forEach(col => {
        let sortKey = col.dataset.sort;
        let indicator = col.querySelector('.sort-indicator');
        if (indicator) {
            if (sortKey === currentSortCol) {
                indicator.innerHTML = currentSortDir === 'asc' ? ' &#9652;' : ' &#9662;';
            } else {
                indicator.innerHTML = '';
            }
        }
    });
}


function updateArrowPositions() {
    let firstVisibleColClass = null;
    for (let colClass of columnOrder) {
        if (columnVisibility[colClass]) {
            firstVisibleColClass = colClass;
            break;
        }
    }
    if (!firstVisibleColClass) return;
    
    document.querySelectorAll('.paper-item').forEach(itemEl => {
        let arrowEl = itemEl.querySelector('.arrow-toggle');
        let targetCol = itemEl.querySelector('.col.' + firstVisibleColClass);
        if (arrowEl && targetCol && arrowEl.parentElement !== targetCol) {
            targetCol.prepend(arrowEl);
        }
    });
}

function applyColumns() {
    // Update headers
    for (let key in columnVisibility) {
        let hCol = document.querySelector(`.list-header-col.${key}`);
        if (hCol) hCol.style.display = columnVisibility[key] ? '' : 'none';
    }
    // Update rows
    document.querySelectorAll('.paper-row-container').forEach(row => {
        for (let key in columnVisibility) {
            let col = row.querySelector(`.${key}`);
            if (col) col.style.display = columnVisibility[key] ? '' : 'none';
        }
    });
    updateArrowPositions();
}

document.addEventListener('DOMContentLoaded', () => {

    // Sidebar Resizer Logic
    const resizerRight = document.getElementById('resizer-right');
    const rightPane = document.getElementById('right-pane');
    if (resizerRight && rightPane) {
        let isResizing = false;
        let startX, startW;
        
        resizerRight.addEventListener('mousedown', (e) => {
            isResizing = true;
            startX = e.clientX;
            // If collapsed, start width is effectively 0
            startW = rightPane.classList.contains('collapsed') ? 0 : rightPane.offsetWidth;
            resizerRight.classList.add('dragging');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
        });
        
        document.addEventListener('mousemove', (e) => {
            if (!isResizing) return;
            // moving mouse left (negative delta) increases right pane width
            let newW = startW - (e.clientX - startX);
            
            if (newW < 100) {
                // Snap to collapse
                rightPane.classList.add('collapsed');
                rightPane.style.width = '0px';
            } else {
                rightPane.classList.remove('collapsed');
                rightPane.style.width = newW + 'px';
            }
        });
        
        document.addEventListener('mouseup', () => {
            if (isResizing) {
                isResizing = false;
                resizerRight.classList.remove('dragging');
                document.body.style.cursor = 'default';
                document.body.style.userSelect = '';
            }
        });
        
        // Double click to toggle collapse
        resizerRight.addEventListener('dblclick', () => {
            if (rightPane.classList.contains('collapsed')) {
                rightPane.classList.remove('collapsed');
                rightPane.style.width = '300px';
            } else {
                rightPane.classList.add('collapsed');
                rightPane.style.width = '0px';
            }
        });
    }


    // Initialize columnOrder from DOM
    document.querySelectorAll('.list-header-col').forEach(col => {
        const cls = Array.from(col.classList).find(c => c.startsWith('col-') && c !== 'col');
        if (cls) columnOrder.push(cls);
    });
    applyColumnOrderStyle();

    let draggedColClass = null;

    document.querySelectorAll('.list-header-col').forEach(col => {
        col.draggable = true;
        col.addEventListener('dblclick', (e) => {
            const rect = col.getBoundingClientRect();
            if (e.clientX > rect.right - 15) {
                const cls = Array.from(col.classList).find(c => c.startsWith('col-') && c !== 'col');
                if (cls) {
                    let propName = '--w-' + cls.substring(4);
                    
                    // Temporarily unlock width to find natural minimum size
                    col.style.width = 'max-content';
                    document.documentElement.style.setProperty(propName, 'max-content');
                    
                    let maxW = col.offsetWidth; // Header natural width
                    
                    document.querySelectorAll('.paper-item .col.' + cls).forEach(cell => {
                        // Some columns like Title have nested flex spans that might need careful measuring
                        let innerText = cell.querySelector('span[style*="text-overflow"]');
                        if (innerText) {
                            maxW = Math.max(maxW, innerText.scrollWidth + 50); // account for arrow, icon, and gaps
                        } else {
                            maxW = Math.max(maxW, cell.scrollWidth);
                        }
                    });
                    
                    // Add a small 8px buffer so text isn't flush against the border
                    maxW += 8;
                    
                    // Lock back to pixels so the manual resize handle still works
                    col.style.width = maxW + 'px';
                    document.documentElement.style.setProperty(propName, maxW + 'px');
                }
            }
        });

        
        col.addEventListener('dragstart', (e) => {
            // Cancel drag if clicked near the right edge (so resize still works)
            const rect = col.getBoundingClientRect();
            if (e.clientX > rect.right - 12) {
                e.preventDefault();
                return;
            }
            
            draggedColClass = Array.from(col.classList).find(c => c.startsWith('col-') && c !== 'col');
            e.dataTransfer.effectAllowed = 'move';
            // Need to set data for Firefox
            e.dataTransfer.setData('text/plain', draggedColClass);
            col.style.opacity = '0.5';
        });
        
        col.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            return false;
        });
        
        col.addEventListener('dragenter', (e) => {
            if (col.classList.contains('list-header-col')) {
                col.classList.add('drag-over');
            }
        });
        
        col.addEventListener('dragleave', (e) => {
            col.classList.remove('drag-over');
        });
        
        col.addEventListener('drop', (e) => {
            e.stopPropagation();
            col.classList.remove('drag-over');
            
            const targetColClass = Array.from(col.classList).find(c => c.startsWith('col-') && c !== 'col');
            if (draggedColClass && targetColClass && draggedColClass !== targetColClass) {
                const fromIdx = columnOrder.indexOf(draggedColClass);
                const toIdx = columnOrder.indexOf(targetColClass);
                
                if (fromIdx > -1 && toIdx > -1) {
                    columnOrder.splice(fromIdx, 1);
                    columnOrder.splice(toIdx, 0, draggedColClass);
                    applyColumnOrderStyle();
                    updateArrowPositions();
                }
            }
            return false;
        });
        
        col.addEventListener('dragend', (e) => {
            col.style.opacity = '1';
            document.querySelectorAll('.list-header-col').forEach(c => c.classList.remove('drag-over'));
        });
    });


    // Metadata Modal Button Listeners
    const modalCancel = document.getElementById('btn-modal-cancel');
    const modalApply = document.getElementById('btn-modal-apply');
    const modalOverlay = document.getElementById('metadata-modal-overlay');

    if (modalCancel) {
        modalCancel.addEventListener('click', () => {
            modalOverlay.style.display = 'none';
        });
    }

    if (modalApply) {
        modalApply.addEventListener('click', async () => {
            const pId = modalOverlay.dataset.paperId;
            if (!pId) return;

            // Gather values from inputs
            const updates = { id: pId };
            const blocks = document.querySelectorAll('.diff-block');
            blocks.forEach(block => {
                const cb = block.querySelector('.diff-apply-cb');
                if (cb && cb.checked) {
                    const input = block.querySelector('.diff-manual-input');
                    if (input) updates[cb.dataset.key] = input.value;
                }
            });
            // Note: untouched fields are omitted, so server must not overwrite them with null.
            // Server's update_full checks `if 'field' in data:`, so omitting is correct!


            modalApply.textContent = 'Saving...';
            modalApply.disabled = true;

            try {
                let saveRes = await fetch('/api/papers/update_full', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(updates)
                });
                
                if (saveRes.ok) {
                    modalOverlay.style.display = 'none';
                    await fetchPapers();
                    // Update details pane if showing this paper
                    let authorsContainer = document.getElementById('authors-list-container');
                    if (authorsContainer && authorsContainer.dataset.id === pId) {
                        setTimeout(() => {
                            let row = document.querySelector(`.paper-row-container[data-id="${pId}"]`);
                            if (row) row.querySelector('.paper-item').click();
                        }, 100);
                    }
                } else {
                    alert('Failed to save metadata.');
                }
            } catch (e) {
                console.error(e);
                alert("Error saving: " + e.message);
            } finally {
                modalApply.textContent = 'Apply Selected Edits';
                modalApply.disabled = false;
            }
        });
    }


    const colObserver = new ResizeObserver(entries => {
        for (let entry of entries) {
            const el = entry.target;
            const w = entry.borderBoxSize ? entry.borderBoxSize[0].inlineSize : el.offsetWidth;
            for (let className of el.classList) {
                if (className.startsWith('col-')) {
                    let propName = '--w-' + className.substring(4);
                    document.documentElement.style.setProperty(propName, w + 'px');
                }
            }
        }
    });

    document.querySelectorAll('.list-header-col').forEach(col => {
        colObserver.observe(col);
    });

    fetchPapers();

    const searchInput = document.getElementById('search-input');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                fetchPapers(e.target.value);
            }, 300);
        });
    }

    
    document.querySelectorAll('.list-header-col').forEach(col => {
        col.addEventListener('click', () => {
            let sortKey = col.dataset.sort;
            if (sortKey) {
                if (currentSortCol === sortKey) {
                    currentSortDir = currentSortDir === 'asc' ? 'desc' : 'asc';
                } else {
                    currentSortCol = sortKey;
                    currentSortDir = 'asc';
                }
                applySorting();
                renderPapersList(currentPapers);
            }
        });
    });

    const listHeader = document.getElementById('list-header-container');
    const colPickerMenu = document.getElementById('column-picker-menu');
    if (listHeader && colPickerMenu) {
        listHeader.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (colPickerMenu.style.display === 'block') {
                colPickerMenu.style.display = 'none';
                return;
            }
            colPickerMenu.style.display = 'block';
            colPickerMenu.style.top = (e.clientY) + 'px';
            colPickerMenu.style.left = (e.clientX) + 'px';
            colPickerMenu.style.right = 'auto'; // Reset right if it was set before
            
            let menuHtml = '';
            const colNames = {
                'col-attachments': 'Attachments',
                'col-title': 'Title',
                'col-creator': 'Creator',
                'col-type': 'Item Type',
                'col-year': 'Year',
                'col-journal': 'Journal/Publication',
                'col-added': 'Date Added',
                'col-citation_key': 'Citation Key',
                'col-pmid': 'PMID',
                'col-pmcid': 'PMCID',
                'col-issn': 'ISSN',
                'col-isbn': 'ISBN',
                'col-doi': 'DOI',
                'col-url': 'URL',
                'col-archive': 'Archive',
                'col-archive_id': 'Archive ID',
                'col-short_title': 'Short Title',
                'col-language': 'Language',
                'col-library_catalog': 'Library Catalog',
                'col-call_number': 'Call Number',
                'col-license': 'License',
                'col-extra': 'Extra',
                'col-series': 'Series',
                'col-edition': 'Edition',
                'col-place': 'Place',
                'col-publisher': 'Publisher',
                'col-volume': 'Volume',
                'col-issue': 'Issue',
                'col-pages': 'Pages'
            };
            for (let key in colNames) {
                menuHtml += `
                    <div class="col-picker-item" data-key="${key}" style="padding: 4px 12px; cursor: pointer; display: flex; align-items: center; gap: 8px;">
                        <input type="checkbox" ${columnVisibility[key] ? 'checked' : ''} style="pointer-events: none;">
                        ${colNames[key]}
                    </div>
                `;
            }
            colPickerMenu.innerHTML = menuHtml;
            
            colPickerMenu.querySelectorAll('.col-picker-item').forEach(item => {
                item.addEventListener('click', (ev) => {
                    ev.stopPropagation();
                    let k = item.dataset.key;
                    columnVisibility[k] = !columnVisibility[k];
                    item.querySelector('input').checked = columnVisibility[k];
                    applyColumns();
                });
            });
        });
        
        document.addEventListener('click', () => {
            colPickerMenu.style.display = 'none';
        });
    }

    const ctxMenu = document.getElementById('context-menu');
    const ctxMenuAtt = document.getElementById('context-menu-att');
    const fileInput = document.getElementById('hidden-file-input');

    // Hide context menu on click anywhere
    document.addEventListener('click', () => {
        if (ctxMenu) ctxMenu.style.display = 'none';
        if (ctxMenuAtt) ctxMenuAtt.style.display = 'none';
    });
    
    document.getElementById('btn-new-item').addEventListener('click', () => {
        fetch('/api/papers/new', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    fetchPapers().then(() => selectPaper(data.id));
                }
            });
    });
    
        if (document.getElementById('cm-delete-paper')) {
        document.getElementById('cm-delete-paper').addEventListener('click', () => {
            if (selectedPaperIds.size > 0) {
                deleteSelectedPapers();
            } else if (contextPaperId) {
                selectedPaperIds.add(contextPaperId);
                deleteSelectedPapers();
            }
        });
    }

    if (document.getElementById('cm-rename-attachment')) {
        document.getElementById('cm-rename-attachment').addEventListener('click', () => {
            if (contextAttId) {
                let newName = prompt("Enter new name for attachment:");
                if (newName) {
                    fetch('/api/rename_attachment', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({id: contextAttId, new_name: newName})
                    }).then(() => fetchPapers());
                }
            }
        });
    }

    if (document.getElementById('cm-delete-attachment')) {
        document.getElementById('cm-delete-attachment').addEventListener('click', () => {
            if (contextAttId) {
                if (confirm("Are you sure you want to delete this attachment?")) {
                    fetch('/api/delete_attachment', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({id: contextAttId})
                    }).then(() => fetchPapers());
                }
            }
        });
    }

    document.getElementById('cm-add-attachment').addEventListener('click', () => {
        if (contextPaperId) {
            fileInput.click();
        }
    });

    fileInput.addEventListener('change', (e) => {
        if (!e.target.files.length || !contextPaperId) return;
        const file = e.target.files[0];
        const formData = new FormData();
        formData.append('file', file);
        formData.append('paper_id', contextPaperId);

        fetch('/api/upload_attachment', {
            method: 'POST',
            body: formData
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                fetchPapers(); // refresh to show new attachment
            } else {
                alert('Upload failed: ' + data.error);
            }
        })
        .catch(err => {
            console.error(err);
            alert('Error uploading file.');
        });
        
        fileInput.value = ''; // reset
    });
});

function fetchPapers(query = '') {
    const url = query ? `/api/papers?q=${encodeURIComponent(query)}` : '/api/papers';
    return fetch(url)
        .then(response => response.json())
        .then(papers => {
            currentPapers = papers;
            renderPapersList(papers);
        })
        .catch(error => {
            console.error('Error loading papers:', error);
            const list = document.getElementById('papers-list');
            if (list) list.innerHTML = '<p style="padding:10px;color:red;">Error loading papers database. Is the server running?</p>';
        });
}

let selectedPaperId = null;
let selectedPaperIds = new Set();
let lastSelectedPaperId = null;

function renderPapersList(papers) {
    currentPapers = papers;
    applySorting();
    const listContainer = document.getElementById('papers-list');
    if (!listContainer) return;
    listContainer.innerHTML = '';

    papers.forEach(paper => {
        const containerEl = document.createElement('div');
        containerEl.className = 'paper-row-container';
        containerEl.dataset.id = paper.id;

        const itemEl = document.createElement('div');
        itemEl.className = 'paper-item';
        
        const colAtt = document.createElement('div');
        colAtt.className = 'col col-attachments';
        if (paper.attachments && paper.attachments.length > 0) {
            colAtt.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#e0402a" stroke-width="1.5"><path d="M3 2v12h10V6l-4-4H3z"/><path d="M9 2v4h4"/><text x="4" y="12" font-size="5" fill="#e0402a" stroke="none" font-weight="bold" font-family="sans-serif">PDF</text></svg>`;
        }
        colAtt.style.display = columnVisibility['col-attachments'] ? '' : 'none';
        itemEl.appendChild(colAtt);

        const colTitle = document.createElement('div');
        colTitle.className = 'col col-title';
        
        const arrowEl = document.createElement('span');
        arrowEl.className = 'arrow-toggle';
        arrowEl.textContent = '▶';
        
        const docIcon = document.createElement('span');
        docIcon.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#666" stroke-width="1.2" style="margin-right:4px;"><path d="M3 2v12h10V6l-4-4H3z"/><path d="M9 2v4h4"/></svg>`;
        
        const titleText = document.createElement('span');
        titleText.textContent = paper.title || 'Untitled';
        titleText.style.flex = '1';
        titleText.style.overflow = 'hidden';
        titleText.style.textOverflow = 'ellipsis';
        titleText.style.whiteSpace = 'nowrap';
        
        colTitle.appendChild(docIcon);
        colTitle.appendChild(titleText);
        itemEl.appendChild(arrowEl); // temp attach so it can be queried
        
        const colCreator = document.createElement('div');
        colCreator.className = 'col col-creator';
        colCreator.textContent = paper.authors || '';
        
        const colType = document.createElement('div');
        colType.className = 'col col-type';
        colType.textContent = paper.item_type || 'Journal Article';
        
        const colJournal = document.createElement('div');
        colJournal.className = 'col col-journal';
        colJournal.textContent = paper.journal || '';
        
        const colYear = document.createElement('div');
        colYear.className = 'col col-year';
        colYear.textContent = paper.year || '';
        
        const colAdded = document.createElement('div');
        colAdded.className = 'col col-added';
        colAdded.textContent = paper.added_date ? paper.added_date.substring(0,10) : '';
        
        itemEl.appendChild(colTitle);
        itemEl.appendChild(colCreator);
        itemEl.appendChild(colType);
        itemEl.appendChild(colYear);
        itemEl.appendChild(colJournal);
        itemEl.appendChild(colAdded);

        // Map column class to the paper object key
        const extraKeys = {
            'col-citation_key': 'citation_key', 'col-pmid': 'pmid', 'col-pmcid': 'pmcid',
            'col-issn': 'issn', 'col-isbn': 'isbn', 'col-doi': 'doi', 'col-url': 'url',
            'col-archive': 'archive', 'col-archive_id': 'archive_id',
            'col-short_title': 'short_title', 'col-language': 'language',
            'col-library_catalog': 'library_catalog', 'col-call_number': 'call_number',
            'col-license': 'license', 'col-extra': 'extra', 'col-series': 'series',
            'col-edition': 'edition', 'col-place': 'place', 'col-publisher': 'publisher',
            'col-volume': 'volume', 'col-issue': 'issue', 'col-pages': 'pages'
        };
        for (let cls in extraKeys) {
            const c = document.createElement('div');
            c.className = `col ${cls}`;
            c.textContent = paper[extraKeys[cls]] || '';
            c.style.display = columnVisibility[cls] ? '' : 'none';
            itemEl.appendChild(c);
        }


        const attachmentsEl = document.createElement('div');
        attachmentsEl.className = 'attachments-list';
        
        if (paper.attachments && paper.attachments.length > 0) {
            paper.attachments.forEach(att => {
                const attItem = document.createElement('div');
                attItem.className = 'attachment-item';
                attItem.innerHTML = `<span class="attachment-icon">📄</span> ${att.name}`;
                attItem.addEventListener('click', (e) => {
                    e.stopPropagation();
                    let fileUrl = att.url.startsWith('http') ? `/api/proxy?url=${encodeURIComponent(att.url)}` : `/pdfs/${encodeURIComponent(att.url)}`;
                    window.open(`/viewer.html?file=${encodeURIComponent(fileUrl)}&paper_id=${paper.id}&title=${encodeURIComponent(paper.title)}`, '_blank');
                });
                attItem.addEventListener('contextmenu', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    contextAttId = att.id;
                    const ctxMenu = document.getElementById('context-menu-att');
                    if (ctxMenu) {
                        ctxMenu.style.display = 'block';
                        ctxMenu.style.left = e.pageX + 'px';
                        ctxMenu.style.top = e.pageY + 'px';
                    }
                });
                attachmentsEl.appendChild(attItem);
            });
        } else {
            arrowEl.style.visibility = 'hidden';
            attachmentsEl.innerHTML = '<div style="color:#888; font-style:italic;">No attachments</div>';
        }

        arrowEl.addEventListener('click', (e) => {
            e.stopPropagation();
            const isExpanded = attachmentsEl.classList.contains('expanded');
            attachmentsEl.classList.toggle('expanded');
            arrowEl.textContent = isExpanded ? '▼' : '▶';
        });

        // Double click on row to open PDF
        containerEl.addEventListener('dblclick', () => {
            if (paper.attachments && paper.attachments.length > 0) {
                let att = paper.attachments[0];
                let fileUrl = att.url.startsWith('http') ? `/api/proxy?url=${encodeURIComponent(att.url)}` : `/pdfs/${encodeURIComponent(att.url)}`;
                window.open(`/viewer.html?file=${encodeURIComponent(fileUrl)}&paper_id=${paper.id}&title=${encodeURIComponent(paper.title)}`, '_blank');
            } else if (paper.pdfUrl) {
                window.open(paper.pdfUrl, '_blank');
            }
        });

        // Click to select
        itemEl.addEventListener('click', (e) => {
            handleRowSelection(e, paper.id);
        });

        // Right-click context menu
        containerEl.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            if (!selectedPaperIds.has(paper.id)) {
                selectPaper(paper.id);
            }
            contextPaperId = paper.id;
            const menu = document.getElementById('context-menu');
            if (menu) {
                menu.style.display = 'block';
                menu.style.left = e.pageX + 'px';
                menu.style.top = e.pageY + 'px';
            }
        });

        containerEl.appendChild(itemEl);
        listContainer.appendChild(containerEl);
        listContainer.appendChild(attachmentsEl);
    });
    applyColumns();
}

document.querySelectorAll('.list-header-col').forEach(col => {
        col.addEventListener('click', () => {
            let sortKey = col.dataset.sort;
            if (sortKey) {
                if (currentSortCol === sortKey) {
                    currentSortDir = currentSortDir === 'asc' ? 'desc' : 'asc';
                } else {
                    currentSortCol = sortKey;
                    currentSortDir = 'asc';
                }
                applySorting();
                renderPapersList(currentPapers);
            }
        });
    });

    const listHeader = document.getElementById('list-header-container');
    const colPickerMenu = document.getElementById('column-picker-menu');
    if (listHeader && colPickerMenu) {
        listHeader.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (colPickerMenu.style.display === 'block') {
                colPickerMenu.style.display = 'none';
                return;
            }
            colPickerMenu.style.display = 'block';
            colPickerMenu.style.top = (e.clientY) + 'px';
            colPickerMenu.style.left = (e.clientX) + 'px';
            colPickerMenu.style.right = 'auto'; // Reset right if it was set before
            
            let menuHtml = '';
            const colNames = {
                'col-attachments': 'Attachments',
                'col-title': 'Title',
                'col-creator': 'Creator',
                'col-type': 'Item Type',
                'col-year': 'Year',
                'col-journal': 'Journal/Publication',
                'col-added': 'Date Added',
                'col-citation_key': 'Citation Key',
                'col-pmid': 'PMID',
                'col-pmcid': 'PMCID',
                'col-issn': 'ISSN',
                'col-isbn': 'ISBN',
                'col-doi': 'DOI',
                'col-url': 'URL',
                'col-archive': 'Archive',
                'col-archive_id': 'Archive ID',
                'col-short_title': 'Short Title',
                'col-language': 'Language',
                'col-library_catalog': 'Library Catalog',
                'col-call_number': 'Call Number',
                'col-license': 'License',
                'col-extra': 'Extra',
                'col-series': 'Series',
                'col-edition': 'Edition',
                'col-place': 'Place',
                'col-publisher': 'Publisher',
                'col-volume': 'Volume',
                'col-issue': 'Issue',
                'col-pages': 'Pages'
            };
            for (let key in colNames) {
                menuHtml += `
                    <div class="col-picker-item" data-key="${key}" style="padding: 4px 12px; cursor: pointer; display: flex; align-items: center; gap: 8px;">
                        <input type="checkbox" ${columnVisibility[key] ? 'checked' : ''} style="pointer-events: none;">
                        ${colNames[key]}
                    </div>
                `;
            }
            colPickerMenu.innerHTML = menuHtml;
            
            colPickerMenu.querySelectorAll('.col-picker-item').forEach(item => {
                item.addEventListener('click', (ev) => {
                    ev.stopPropagation();
                    let k = item.dataset.key;
                    columnVisibility[k] = !columnVisibility[k];
                    item.querySelector('input').checked = columnVisibility[k];
                    applyColumns();
                });
            });
        });
        
        document.addEventListener('click', () => {
            colPickerMenu.style.display = 'none';
        });
    }

function handleRowSelection(e, id) {
    if (e && (e.metaKey || e.ctrlKey)) {
        if (selectedPaperIds.has(id)) {
            selectedPaperIds.delete(id);
        } else {
            selectedPaperIds.add(id);
        }
        lastSelectedPaperId = id;
    } else if (e && e.shiftKey && lastSelectedPaperId) {
        let allIds = currentPapers.map(p => p.id);
        let idx1 = allIds.indexOf(lastSelectedPaperId);
        let idx2 = allIds.indexOf(id);
        if (idx1 !== -1 && idx2 !== -1) {
            let start = Math.min(idx1, idx2);
            let end = Math.max(idx1, idx2);
            selectedPaperIds.clear();
            for (let i = start; i <= end; i++) {
                selectedPaperIds.add(allIds[i]);
            }
        }
    } else {
        selectedPaperIds.clear();
        selectedPaperIds.add(id);
        lastSelectedPaperId = id;
    }
    updateSelectionUI();
}

function updateSelectionUI() {
    document.querySelectorAll('.paper-row-container').forEach(container => {
        const itemEl = container.querySelector('.paper-item');
        if (selectedPaperIds.has(container.dataset.id)) {
            itemEl.classList.add('selected');
        } else {
            itemEl.classList.remove('selected');
        }
    });
    
    if (selectedPaperIds.size === 1) {
        let id = Array.from(selectedPaperIds)[0];
        const paper = currentPapers.find(p => p.id == id);
        if (paper) renderDetails(paper);
    } else if (selectedPaperIds.size > 1) {
        const detailsContainer = document.getElementById('paper-details');
        if (detailsContainer) detailsContainer.innerHTML = `<div class="placeholder">${selectedPaperIds.size} items selected</div>`;
        const titleDisplay = document.getElementById('details-title-display');
        if (titleDisplay) titleDisplay.textContent = `${selectedPaperIds.size} items selected`;
    } else {
        const detailsContainer = document.getElementById('paper-details');
        if (detailsContainer) detailsContainer.innerHTML = `<div class="placeholder">Select an item to view its details.</div>`;
        const titleDisplay = document.getElementById('details-title-display');
        if (titleDisplay) titleDisplay.textContent = 'Select an item';
    }
    
    selectedPaperId = selectedPaperIds.size === 1 ? Array.from(selectedPaperIds)[0] : null;
}

function selectPaper(id) {
    selectedPaperIds.clear();
    selectedPaperIds.add(id);
    lastSelectedPaperId = id;
    updateSelectionUI();
}

async function deleteSelectedPapers() {
    if (!confirm(`Are you sure you want to delete ${selectedPaperIds.size} item(s)? This will permanently delete the entries and any attached files.`)) return;
    
    for (let id of selectedPaperIds) {
        await fetch('/api/papers/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: id})
        });
    }
    selectedPaperIds.clear();
    lastSelectedPaperId = null;
    updateSelectionUI();
    fetchPapers();
}

document.addEventListener('keydown', (e) => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
    if ((e.key === 'Backspace' || e.key === 'Delete') && selectedPaperIds.size > 0) {
        e.preventDefault();
        deleteSelectedPapers();
    }
});


function renderDetails(paper) {
    const detailsContainer = document.getElementById('paper-details');
    const titleDisplay = document.getElementById('details-title-display');
    
    if (titleDisplay) {
        titleDisplay.textContent = paper.title || 'Untitled';
    }
    
    let html = '';
    
    let typeOptions = `
        <select id="item-type-select" data-id="${paper.id}" style="border: 1px solid transparent; background: transparent; font-size: 13px; font-family: inherit; outline: none; margin-left: -4px;">
            <option value="journalArticle" ${paper.item_type === 'journalArticle' ? 'selected' : ''}>Journal Article</option>
            <option value="book" ${paper.item_type === 'book' ? 'selected' : ''}>Book</option>
            <option value="bookSection" ${paper.item_type === 'bookSection' ? 'selected' : ''}>Book Section</option>
            <option value="preprint" ${paper.item_type === 'preprint' ? 'selected' : ''}>Preprint</option>
            <option value="report" ${paper.item_type === 'report' ? 'selected' : ''}>Report</option>
        </select>
    `;
    
    let fields = [
        { label: 'Item Type', is_select: true, html: typeOptions },
        { label: 'Title', db_key: 'title', value: paper.title || '', editable: true },
        { label: 'Author', db_key: 'authors', value: paper.authors || '', editable: true }
    ];
    
    let itype = paper.item_type || 'journalArticle';
    
    if (itype === 'journalArticle') {
        fields.push({ label: 'Publication', db_key: 'journal', value: paper.journal || '', editable: true });
        fields.push({ label: 'Volume', db_key: 'volume', value: paper.volume || '', editable: true });
        fields.push({ label: 'Issue', db_key: 'issue', value: paper.issue || '', editable: true });
        fields.push({ label: 'Pages', db_key: 'pages', value: paper.pages || '', editable: true });
    } else if (itype === 'book') {
        fields.push({ label: 'Publisher', db_key: 'publisher', value: paper.publisher || '', editable: true });
        fields.push({ label: 'Volume', db_key: 'volume', value: paper.volume || '', editable: true });
        fields.push({ label: 'ISBN', db_key: 'isbn', value: paper.isbn || '', editable: true });
    } else if (itype === 'bookSection') {
        fields.push({ label: 'Book Title', db_key: 'journal', value: paper.journal || '', editable: true });
        fields.push({ label: 'Publisher', db_key: 'publisher', value: paper.publisher || '', editable: true });
        fields.push({ label: 'Pages', db_key: 'pages', value: paper.pages || '', editable: true });
        fields.push({ label: 'ISBN', db_key: 'isbn', value: paper.isbn || '', editable: true });
    } else if (itype === 'preprint' || itype === 'report') {
        fields.push({ label: 'Institution/Archive', db_key: 'journal', value: paper.journal || '', editable: true });
    }
    
    fields.push({ label: 'Date', db_key: 'year', value: paper.year || '', editable: true });
    fields.push({ label: 'Series', db_key: 'series', value: paper.series || '', editable: true });
    fields.push({ label: 'Edition', db_key: 'edition', value: paper.edition || '', editable: true });
    fields.push({ label: 'Place', db_key: 'place', value: paper.place || '', editable: true });
    fields.push({ label: 'DOI', db_key: 'doi', value: paper.doi || '', editable: true });
    fields.push({ label: 'URL', db_key: 'url', value: paper.url || '', editable: true });
    fields.push({ label: 'Citation Key', db_key: 'citation_key', value: paper.citation_key || '', editable: true });
    fields.push({ label: 'PMID', db_key: 'pmid', value: paper.pmid || '', editable: true });
    fields.push({ label: 'PMCID', db_key: 'pmcid', value: paper.pmcid || '', editable: true });
    fields.push({ label: 'ISSN', db_key: 'issn', value: paper.issn || '', editable: true });
    fields.push({ label: 'Archive', db_key: 'archive', value: paper.archive || '', editable: true });
    fields.push({ label: 'Archive ID', db_key: 'archive_id', value: paper.archive_id || '', editable: true });
    fields.push({ label: 'Short Title', db_key: 'short_title', value: paper.short_title || '', editable: true });
    fields.push({ label: 'Language', db_key: 'language', value: paper.language || '', editable: true });
    fields.push({ label: 'Library Catalog', db_key: 'library_catalog', value: paper.library_catalog || '', editable: true });
    fields.push({ label: 'Call Number', db_key: 'call_number', value: paper.call_number || '', editable: true });
    fields.push({ label: 'License', db_key: 'license', value: paper.license || '', editable: true });
    fields.push({ label: 'Extra', db_key: 'extra', value: paper.extra || '', editable: true });
    fields.push({ label: 'Abstract', db_key: 'abstract', value: paper.abstract || '', editable: true });
    fields.push({ label: 'Attachments', value: paper.attachments ? `${paper.attachments.length} files` : '0 files', editable: false });


    fields.forEach(field => {
        if (field.db_key === 'authors') {
            let authorList = field.value ? field.value.split(';').map(s => s.trim()).filter(s => s) : [];
            if (authorList.length === 0) authorList = [""];
            
            html += `<div class="detail-row" style="align-items: flex-start; flex-direction: column;">`;
            html += `<span class="detail-label" style="margin-bottom: 4px;">Authors</span>`;
            html += `<div id="authors-list-container" data-id="${paper.id}" style="display: flex; flex-direction: column; gap: 4px; width: 100%; box-sizing: border-box; overflow: hidden;">`;
            
            authorList.forEach((authStr, idx) => {
                let parts = authStr.split(',');
                let last = (parts[0] || '').trim();
                let first = (parts[1] || '').trim();
                
                html += `
                <div class="author-edit-row" style="display: flex; gap: 2px; align-items: center; padding: 2px 0;">
                    <input type="text" class="author-last" placeholder="Last" value="${last.replace(/"/g, '&quot;')}" style="flex: 1; min-width: 0; width: 40%; border: 1px solid transparent; background: transparent; padding: 2px; font-size: 13px; font-family: inherit; outline: none;">
                    <input type="text" class="author-first" placeholder="First" value="${first.replace(/"/g, '&quot;')}" style="flex: 1; min-width: 0; width: 40%; border: 1px solid transparent; background: transparent; padding: 2px; font-size: 13px; font-family: inherit; outline: none;">
                    <div class="author-buttons" style="display: flex;">
                        <button class="author-add" style="background:none; border:none; cursor:pointer; font-size: 16px; line-height: 1; padding: 0 4px; color: #666;" title="Add author">+</button>
                        <button class="author-remove" style="background:none; border:none; cursor:pointer; font-size: 16px; line-height: 1; padding: 0 4px; color: #666;" title="Remove author">&minus;</button>
                    </div>
                </div>`;
            });
            
            html += `</div></div>`;
            return;
        }

        if (field.is_select) {
            html += `
                <div class="detail-row">
                    <span class="detail-label">${field.label}</span>
                    <span class="detail-value" style="padding: 2px;">${field.html}</span>
                </div>
            `;
            return;
        }

        let editAttr = field.editable ? `contenteditable="true" data-key="${field.db_key}" data-id="${paper.id}" class="detail-value editable-field"` : `class="detail-value"`;
        html += `
            <div class="detail-row">
                <span class="detail-label">${field.label}</span>
                <span ${editAttr} style="border: 1px solid transparent; padding: 2px; outline: none;">${field.value}</span>
            </div>
        `;
    });

    detailsContainer.innerHTML = html;
    
    // BIND AUTHORS
    const authorsContainer = detailsContainer.querySelector('#authors-list-container');
    if (authorsContainer) {
        let paperId = authorsContainer.dataset.id;
        
        let authorSaveTimeout = null;
        function saveAuthors() {
            if (authorSaveTimeout) clearTimeout(authorSaveTimeout);
            authorSaveTimeout = setTimeout(() => {
                executeSaveAuthors();
            }, 500);
        }
        
        function executeSaveAuthors() {
            let rows = authorsContainer.querySelectorAll('.author-edit-row');
            let authorStrings = [];
            rows.forEach(row => {
                let last = row.querySelector('.author-last').value.trim();
                let first = row.querySelector('.author-first').value.trim();
                if (last || first) {
                    authorStrings.push(first ? `${last}, ${first}` : last);
                }
            });
            
            let newValue = authorStrings.join('; ');
            
            fetch('/api/papers/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    id: paperId,
                    field: 'authors',
                    value: newValue
                })
            }).then((res) => {
                if (!res.ok) { console.error("Failed to update db"); return; }
                let row = document.querySelector(`.paper-row-container[data-id="${paperId}"]`);
                if (row) {
                    row.querySelector('.col-creator').textContent = newValue;
                }
                let p = currentPapers.find(p => p.id === paperId);
                if (p) p.authors = newValue;
            });
        }
        
        authorsContainer.addEventListener('input', (e) => {
            if (e.target.tagName === 'INPUT') {
                saveAuthors();
            }
        });
        
        authorsContainer.addEventListener('click', (e) => {
            if (e.target.classList.contains('author-add')) {
                let newRow = document.createElement('div');
                newRow.className = 'author-edit-row';
                newRow.style.cssText = 'display: flex; gap: 2px; align-items: center; padding: 2px 0;';
                newRow.innerHTML = `
                    <input type="text" class="author-last" placeholder="Last" value="" style="flex: 1; min-width: 0; width: 40%; border: 1px solid transparent; background: transparent; padding: 2px; font-size: 13px; font-family: inherit; outline: none;">
                    <input type="text" class="author-first" placeholder="First" value="" style="flex: 1; min-width: 0; width: 40%; border: 1px solid transparent; background: transparent; padding: 2px; font-size: 13px; font-family: inherit; outline: none;">
                    <div class="author-buttons" style="display: flex;">
                        <button class="author-add" style="background:none; border:none; cursor:pointer; font-size: 16px; line-height: 1; padding: 0 4px; color: #666;" title="Add author">+</button>
                        <button class="author-remove" style="background:none; border:none; cursor:pointer; font-size: 16px; line-height: 1; padding: 0 4px; color: #666;" title="Remove author">&minus;</button>
                    </div>
                `;
                e.target.closest('.author-edit-row').after(newRow);
                saveAuthors();
            } else if (e.target.classList.contains('author-remove')) {
                let row = e.target.closest('.author-edit-row');
                if (authorsContainer.querySelectorAll('.author-edit-row').length > 1) {
                    row.remove();
                    saveAuthors();
                } else {
                    row.querySelector('.author-last').value = '';
                    row.querySelector('.author-first').value = '';
                    saveAuthors();
                }
            }
        });
    }

    // Bind Item Type changes
    let typeSelect = detailsContainer.querySelector('#item-type-select');
    if (typeSelect) {
        typeSelect.addEventListener('change', (e) => {
            let newValue = e.target.value;
            let paperId = e.target.dataset.id;
            fetch('/api/papers/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: paperId, field: 'item_type', value: newValue })
            }).then(() => {
                let p = currentPapers.find(x => x.id == paperId);
                if (p) p.item_type = newValue;
                let row = document.querySelector(`.paper-row-container[data-id="${paperId}"]`);
                if (row) {
                    let colType = row.querySelector('.col-type');
                    if (colType) colType.textContent = newValue;
                }
                renderDetails(p); // re-render to update dynamic fields
            });
        });
    }

    // Bind other edits
    detailsContainer.querySelectorAll('.editable-field').forEach(el => {
        el.addEventListener('mouseenter', () => el.style.border = '1px solid #ccc');
        el.addEventListener('mouseleave', () => {
            if (document.activeElement !== el) el.style.border = '1px solid transparent';
        });
        el.addEventListener('focus', () => el.style.border = '1px solid #0066cc');
        el.addEventListener('blur', (e) => {
            el.style.border = '1px solid transparent';
            const newValue = e.target.innerText.trim();
            const fieldKey = e.target.dataset.key;
            const paperId = e.target.dataset.id;
            
            fetch('/api/papers/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: paperId, field: fieldKey, value: newValue })
            }).then(() => {
                if (fieldKey === 'title') titleDisplay.textContent = newValue || 'Untitled';
                
                let p = currentPapers.find(x => x.id == paperId);
                if (p) p[fieldKey] = newValue;

                const row = document.querySelector(`.paper-row-container[data-id="${paperId}"]`);
                if (row) {
                    if (fieldKey === 'title') row.querySelector('.col-title span:last-child').textContent = newValue || 'Untitled';
                    if (fieldKey === 'year') row.querySelector('.col-year').textContent = newValue || '';
                }
            });
        });
    });
}


document.getElementById('btn-magic-wand').addEventListener('click', async () => {
    let identifier = prompt('Enter a DOI (e.g. 10.1038/nature12373):');
    if (!identifier) return;
    
    // Normalize DOI
    let doi = identifier.trim();
    if (doi.startsWith('http')) {
        try {
            let url = new URL(doi);
            doi = url.pathname.replace(/^\/+/, '');
        } catch(e) {}
    }
    doi = doi.replace(/^doi:/i, '').trim();
    doi = doi.replace(/^https?:\/\/doi\.org\//i, '').trim();
    
    try {
        let res = await fetch(`https://api.crossref.org/works/${encodeURIComponent(doi)}`, { headers: { 'User-Agent': 'ZoteroClone/1.0 (mailto:lorfioroni@example.com)' } });
        if (!res.ok) throw new Error('Failed to fetch metadata from Crossref.');
        
        let data = await res.json();
        let item = data.message;
        
        let title = item.title ? item.title[0] : 'Unknown Title';
        
        let allAuthors = getAllKnownAuthors();
        let authors = '';
        if (item.author) {
            authors = item.author.map(a => resolveAuthor(a.family, a.given, allAuthors)).join('; ');
        }
        if (doi) {
            let oa = await fetchOpenAlexAuthors(doi, allAuthors);
            if (oa && oa.length > authors.length) {
                authors = oa;
            }
        }
        
        let year = null;
        if (item['published-print'] && item['published-print']['date-parts']) {
            year = item['published-print']['date-parts'][0][0];
        } else if (item.created && item.created['date-parts']) {
            year = item.created['date-parts'][0][0];
        }
        
        let journal = '';
        if (item['container-title'] && item['container-title'].length > 0) {
            journal = item['container-title'][0];
        }
        
        let abstract = item.abstract || '';
        let fetchedDoi = item.DOI || doi;
        
        let saveRes = await fetch('/api/papers/new_full', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                title, authors, year, journal, abstract, doi: fetchedDoi
            })
        });
        
        if (saveRes.ok) {
            fetchPapers();
        } else {
            alert('Failed to save paper to database.');
        }
        
    } catch (err) {
        console.error(err);
        alert('Error: ' + err.message + '\n(Check the Developer Console for details)');
    }
});


document.getElementById('cm-fetch-metadata').addEventListener('click', async () => {
    document.getElementById('context-menu').style.display = 'none';
    if (!contextPaperId) return;
    
    let paper = currentPapers.find(p => p.id === contextPaperId);
    if (!paper) return;
    if (!paper.title && !paper.doi) {
        alert("Paper must have a title or DOI to fetch metadata.");
        return;
    }
    
    document.body.style.cursor = 'wait';
    try {
        let fetchUrl = '';
        let item = null;
        
        if (paper.doi) {
            let cleanDoi = paper.doi.replace(/^https?:\/\/doi\.org\//i, '').replace(/^doi:/i, '').trim();
            fetchUrl = `https://api.crossref.org/works/${encodeURIComponent(cleanDoi)}`;
            let res = await fetch(fetchUrl);
            if (!res.ok) throw new Error("Crossref API failed for DOI");
            let data = await res.json();
            item = data.message;
        } else {
            fetchUrl = `https://api.crossref.org/works?query.title=${encodeURIComponent(paper.title)}&select=title,author,published-print,created,container-title,abstract,DOI&rows=1`;
            let res = await fetch(fetchUrl);
            if (!res.ok) throw new Error("Crossref API failed for title search");
            let data = await res.json();
            let items = data.message.items;
            if (!items || items.length === 0) {
                alert("No metadata found for this title.");
                return;
            }
            item = items[0];
        }
        
        let newTitle = item.title ? item.title[0] : '';
        let allAuthors = getAllKnownAuthors();
        let newAuthors = '';
        if (item.author) {
            newAuthors = item.author.map(a => resolveAuthor(a.family, a.given, allAuthors)).join('; ');
        }
        let cleanDoiForOa = (item.DOI || paper.doi || '').replace(/^https?:\/\/(dx\.)?doi\.org\//i, '').replace(/^doi:/i, '').trim();
        if (cleanDoiForOa) {
            let oa = await fetchOpenAlexAuthors(cleanDoiForOa, allAuthors);
            if (oa && oa.length > newAuthors.length) {
                newAuthors = oa;
            }
        }
        
        let newYear = null;
        if (item['published-print'] && item['published-print']['date-parts']) {
            newYear = item['published-print']['date-parts'][0][0];
        } else if (item.created && item.created['date-parts']) {
            newYear = item.created['date-parts'][0][0];
        }
        
        let newJournal = '';
        if (item['container-title'] && item['container-title'].length > 0) {
            newJournal = item['container-title'][0];
        }
        
        let newAbstract = item.abstract || '';
        let newDoi = item.DOI || '';
        
        let newCitationKey = '';
        if (newAuthors) {
            let firstAuth = newAuthors.split(';')[0].split(',')[0].trim().replace(/[^a-zA-Z]/g, '').toLowerCase();
            let firstWord = newTitle.split(' ').find(w => w.length > 3) || newTitle.split(' ')[0] || '';
            firstWord = firstWord.replace(/[^a-zA-Z]/g, '');
            firstWord = firstWord.charAt(0).toUpperCase() + firstWord.slice(1).toLowerCase();
            newCitationKey = `${firstAuth}${firstWord}${newYear || ''}`;
        }
        let newPmid = item.PMID || '';
        let newPmcid = item.PMCID || '';
        let newIssn = item.ISSN ? item.ISSN.join(', ') : '';
        let newArchive = item.archive || '';
        let newArchiveId = '';
        let newShortTitle = item['short-title'] && item['short-title'].length > 0 ? item['short-title'][0] : '';
        let newLanguage = item.language || '';
        let newLicense = item.license && item.license.length > 0 ? item.license[0].URL : '';
        
        // Render Diff Modal
        const fields = [
            { key: 'title', label: 'Title', current: paper.title || '', fetched: newTitle, isTextarea: true },
            { key: 'authors', label: 'Authors', current: paper.authors || '', fetched: newAuthors, isTextarea: true },
            { key: 'year', label: 'Year', current: paper.year || '', fetched: newYear || '' },
            { key: 'journal', label: 'Journal', current: paper.journal || '', fetched: newJournal },
            { key: 'doi', label: 'DOI', current: paper.doi || '', fetched: newDoi },
            { key: 'citation_key', label: 'Citation Key', current: paper.citation_key || '', fetched: newCitationKey },
            { key: 'pmid', label: 'PMID', current: paper.pmid || '', fetched: newPmid },
            { key: 'pmcid', label: 'PMCID', current: paper.pmcid || '', fetched: newPmcid },
            { key: 'issn', label: 'ISSN', current: paper.issn || '', fetched: newIssn },
            { key: 'archive', label: 'Archive', current: paper.archive || '', fetched: newArchive },
            { key: 'archive_id', label: 'Archive ID', current: paper.archive_id || '', fetched: newArchiveId },
            { key: 'short_title', label: 'Short Title', current: paper.short_title || '', fetched: newShortTitle },
            { key: 'language', label: 'Language', current: paper.language || '', fetched: newLanguage },
            { key: 'license', label: 'License', current: paper.license || '', fetched: newLicense },
            { key: 'abstract', label: 'Abstract', current: paper.abstract || '', fetched: newAbstract, isTextarea: true }
        ];
        
        const tbody = document.getElementById('metadata-diff-body');
        tbody.innerHTML = '';
        
        let changedCount = 0;
        
        fields.forEach(f => {
            const suggested = f.fetched ? String(f.fetched) : String(f.current || "");
            const currentStr = String(f.current || "");
            
            if (suggested === currentStr) return; // ONLY show changed fields
            changedCount++;
            
            const diff = diffWords(currentStr, suggested);
            
            const block = document.createElement('div');
            block.className = 'diff-block';
            
            const header = document.createElement('div');
            header.className = 'diff-header';
            
            const labelWrap = document.createElement('label');
            labelWrap.className = 'diff-cb-label';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = true;
            cb.className = 'diff-apply-cb';
            cb.dataset.key = f.key;
            
            labelWrap.appendChild(cb);
            labelWrap.appendChild(document.createTextNode(' ' + f.label));
            
            const btnEdit = document.createElement('button');
            btnEdit.className = 'btn-ghost';
            btnEdit.textContent = 'Edit Manually';
            
            header.appendChild(labelWrap);
            header.appendChild(btnEdit);
            
            const viewer = document.createElement('div');
            viewer.className = 'diff-viewer';
            
            const dOld = document.createElement('div');
            dOld.className = 'diff-old';
            dOld.innerHTML = diff.old || '<em>(empty)</em>';
            
            const dNew = document.createElement('div');
            dNew.className = 'diff-new';
            dNew.innerHTML = diff.new || '<em>(empty)</em>';
            
            viewer.appendChild(dOld);
            viewer.appendChild(dNew);
            
            const editor = document.createElement('div');
            editor.className = 'diff-editor';
            editor.style.display = 'none';
            
            let inputEl = document.createElement('textarea');
            inputEl.className = 'diff-new-input diff-manual-input';
            inputEl.value = suggested;
            inputEl.dataset.key = f.key;
            editor.appendChild(inputEl);
            
            btnEdit.onclick = () => {
                if (editor.style.display === 'none') {
                    editor.style.display = 'block';
                    viewer.style.display = 'none';
                    btnEdit.textContent = 'View Diff';
                } else {
                    editor.style.display = 'none';
                    viewer.style.display = 'block';
                    btnEdit.textContent = 'Edit Manually';
                    // Recompute diff just in case they changed it
                    const newDiff = diffWords(currentStr, inputEl.value);
                    dOld.innerHTML = newDiff.old || '<em>(empty)</em>';
                    dNew.innerHTML = newDiff.new || '<em>(empty)</em>';
                }
            };
            
            block.appendChild(header);
            block.appendChild(viewer);
            block.appendChild(editor);
            
            tbody.appendChild(block);
        });
        
        if (changedCount === 0) {
            tbody.innerHTML = '<div style="padding: 20px; text-align: center; color: #888;">No changes found.</div>';
        }
        
        const overlay = document.getElementById('metadata-modal-overlay');
        overlay.style.display = 'flex';
        overlay.dataset.paperId = contextPaperId;
        
    } catch (e) {
        console.error(e);
        alert("Error fetching metadata: " + e.message);
    } finally {
        document.body.style.cursor = 'default';
    }
});

document.getElementById('cm-find-pdf').addEventListener('click', async () => {
    document.getElementById('context-menu').style.display = 'none';
    if (!contextPaperId) return;
    
    let paper = currentPapers.find(p => p.id === contextPaperId);
    if (!paper || !paper.title) return;
    
    document.body.style.cursor = 'wait';
    
    try {
        let res = await fetch('/api/papers/find_pdf', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: contextPaperId, title: paper.title, doi: paper.doi })
        });
        
        let data = await res.json();
        if (data.status === 'success') {
            fetchPapers();
            alert("PDF found and attached successfully!");
        } else {
            alert(data.message || "Could not find a free PDF online.");
        }
    } catch (e) {
        console.error(e);
        alert("Error finding PDF: " + e.message);
    } finally {
        document.body.style.cursor = 'default';
    }
});
