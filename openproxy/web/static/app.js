// OpenProxy — Alpine.js helpers for the web UI

function providersData() {
    return {
        providers: [],
        collapsed: {},
        loading: true,
        init() { this.fetchProviders(); },
        toggleCollapse(id) {
            this.collapsed = {...this.collapsed, [id]: !this.collapsed[id]};
        },
        isCollapsed(id) { return this.collapsed[id] === true; },

        async fetchProviders() {
            this.loading = true;
            try {
                const resp = await fetch('/api/providers');
                this.providers = await resp.json();
                // Collapse all model lists by default
                for (const p of this.providers) {
                    this.collapsed[p.id] = true;
                }
            } catch (e) { console.error('Failed to fetch providers:', e); }
            this.loading = false;
        },
        statusClass(provider) {
            if (!provider.is_active) return 'disabled';
            if (provider.cooldown_until && new Date(provider.cooldown_until) > new Date()) return 'cooldown';
            return 'active';
        },
        async toggleProvider(id) {
            const resp = await fetch(`/api/providers/${id}/toggle`, { method: 'POST' });
            if (resp.ok) {
                const data = await resp.json();
                const p = this.providers.find(x => x.id === id);
                if (p) p.is_active = data.is_active;
            }
        },
        async deleteProvider(id, name) {
            if (!confirm(`Delete provider "${name}"? This cannot be undone.`)) return;
            const resp = await fetch(`/api/providers/${id}`, { method: 'DELETE' });
            if (resp.ok) this.providers = this.providers.filter(p => p.id !== id);
        },
        async scanProvider(id, btn) {
            btn.disabled = true;
            btn.textContent = '⏳ Scanning...';
            try {
                const resp = await fetch(`/api/providers/${id}/detect-models`, { method: 'POST' });
                const data = await resp.json();
                if (resp.ok) { await this.fetchProviders(); }
                else { alert('Scan failed: ' + (data.detail || 'Unknown error')); }
            } catch (e) { alert('Network error: ' + e.message); }
            finally { btn.disabled = false; btn.textContent = '🔍 Scan'; }
        },
        async addModel(providerId, name) {
            if (!name || !name.trim()) return;
            const resp = await fetch(`/api/providers/${providerId}/models`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: name.trim()}),
            });
            if (resp.ok) {
                const data = await resp.json();
                const p = this.providers.find(x => x.id === providerId);
                if (p) {
                    if (!p.models) p.models = [];
                    p.models.push({
                        id: data.id, name: data.name, is_enabled: data.is_enabled,
                        is_auto_detected: data.is_auto_detected, created_at: data.created_at,
                    });
                }
            } else { const err = await resp.json(); alert(err.detail || 'Failed to add model'); }
        },
        async toggleModel(providerId, modelId) {
            const resp = await fetch(`/api/providers/${providerId}/models/${modelId}/toggle`, { method: 'PUT' });
            if (resp.ok) {
                const data = await resp.json();
                const p = this.providers.find(x => x.id === providerId);
                if (p) { const m = p.models.find(x => x.id === modelId); if (m) m.is_enabled = data.is_enabled; }
            }
        },
        async deleteModel(providerId, modelId) {
            const resp = await fetch(`/api/providers/${providerId}/models/${modelId}`, { method: 'DELETE' });
            if (resp.ok) {
                const p = this.providers.find(x => x.id === providerId);
                if (p) p.models = p.models.filter(m => m.id !== modelId);
            }
        },
    };
}

function modelSetsData() {
    return {
        sets: [],
        providers: [],
        selectedSetId: null,
        loading: true,
        newSetName: '',
        newSetDefault: false,
        newEntryProvider: '',
        newEntryModel: '',
        editingName: false,
        renameValue: '',
        availableModels: [],

        init() { this.fetchAll(); },
        async fetchAll() {
            this.loading = true;
            const prevId = this.selectedSetId;
            try {
                const [a, b] = await Promise.all([fetch('/api/model-sets'), fetch('/api/providers')]);
                this.sets = await a.json();
                this.providers = await b.json();
                // Preserve selected set, or fall back to first
                if (this.sets.find(s => s.id === prevId)) {
                    this.selectedSetId = prevId;
                } else if (this.sets.length > 0) {
                    this.selectedSetId = this.sets[0].id;
                } else {
                    this.selectedSetId = null;
                }
            } catch (e) { console.error(e); }
            this.loading = false;
        },
        get selectedSet() { return this.sets.find(s => s.id === this.selectedSetId) || null; },
        selectSet(id) { this.selectedSetId = id; },

        startRename() { this.renameValue = this.selectedSet?.name || ''; this.editingName = true; },
        async submitRename() {
            if (!this.renameValue.trim() || !this.selectedSet) return;
            const resp = await fetch(`/api/model-sets/${this.selectedSet.id}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: this.renameValue.trim(), is_default: this.selectedSet.is_default}),
            });
            if (resp.ok) { this.editingName = false; await this.fetchAll(); }
            else { const err = await resp.json(); alert(err.detail || 'Failed'); }
        },

        onProviderChange() {
            this.newEntryModel = '';
            this.availableModels = [];
            if (!this.newEntryProvider) return;
            const p = this.providers.find(x => x.id === parseInt(this.newEntryProvider));
            if (p && p.models) { this.availableModels = p.models.filter(m => m.is_enabled).map(m => m.name); }
        },

        openCreate() { this.newSetName = ''; this.newSetDefault = false; document.getElementById('create-modal').showModal(); },
        closeCreate() { document.getElementById('create-modal').close(); },
        async submitCreate() {
            const resp = await fetch('/api/model-sets', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: this.newSetName, is_default: this.newSetDefault}),
            });
            if (resp.ok) { this.closeCreate(); await this.fetchAll(); }
            else { const err = await resp.json(); alert(err.detail || 'Failed'); }
        },
        async deleteSet(id, name) {
            if (!confirm(`Delete "${name}"?`)) return;
            if ((await fetch(`/api/model-sets/${id}`, {method: 'DELETE'})).ok) {
                await this.fetchAll();
            }
        },
        async forceSync(id) {
            const btn = document.activeElement;
            if (btn) btn.disabled = true;
            try {
                const resp = await fetch(`/api/model-sets/${id}/sync`, {method: 'POST'});
                if (resp.ok) {
                    // Merge synced data back into sets
                    const synced = await resp.json();
                    const idx = this.sets.findIndex(s => s.id === id);
                    if (idx !== -1) this.sets[idx] = synced;
                } else {
                    const err = await resp.json();
                    alert(err.detail || 'Sync failed');
                }
            } catch (e) { alert('Network error: ' + e.message); }
            finally { if (btn) btn.disabled = false; }
        },
        formatOverrides(overrides) {
            if (!overrides || Object.keys(overrides).length === 0) return 'none';
            return Object.entries(overrides).map(([k, v]) => `${k}=${v}`).join(' ');
        },
        formatTimeAgo(ts) {
            if (!ts) return 'never';
            const diff = Date.now() - new Date(ts).getTime();
            const mins = Math.floor(diff / 60000);
            if (mins < 1) return 'just now';
            if (mins < 60) return mins + 'm ago';
            const hours = Math.floor(mins / 60);
            if (hours < 24) return hours + 'h ago';
            const days = Math.floor(hours / 24);
            return days + 'd ago';
        },
        startEditOverrides(entry) {
            entry._editingOverrides = true;
            entry._overridesText = JSON.stringify(entry.overrides || {});
        },
        cancelEditOverrides(entry) {
            entry._editingOverrides = false;
            delete entry._overridesText;
        },
        async saveOverrides(entry) {
            let parsed;
            try { parsed = JSON.parse(entry._overridesText); }
            catch (e) { alert('Invalid JSON: ' + e.message); return; }
            const resp = await fetch(`/api/model-sets/${this.selectedSetId}/entries/${entry.id}/overrides`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({overrides: parsed}),
            });
            if (resp.ok) {
                const data = await resp.json();
                entry.overrides = data.overrides;
                entry._editingOverrides = false;
                delete entry._overridesText;
            } else {
                const err = await resp.json();
                alert(err.detail || 'Failed');
            }
        },
        async setDefault(id) {
            if ((await fetch(`/api/model-sets/${id}/default`, {method: 'POST'})).ok) await this.fetchAll();
        },
        async addEntry() {
            if (!this.newEntryProvider || !this.newEntryModel) return;
            const resp = await fetch(`/api/model-sets/${this.selectedSetId}/entries`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({provider_id: parseInt(this.newEntryProvider), model_name: this.newEntryModel}),
            });
            if (resp.ok) { this.newEntryProvider = ''; this.newEntryModel = ''; await this.fetchAll(); }
            else { const err = await resp.json(); alert(err.detail || 'Failed'); }
        },
        async toggleEntry(entryId) {
            const resp = await fetch(`/api/model-sets/${this.selectedSetId}/entries/${entryId}/toggle`, {method: 'PUT'});
            if (resp.ok) {
                const data = await resp.json();
                const e = this.selectedSet?.entries.find(x => x.id === entryId);
                if (e) e.is_enabled = data.is_enabled;
            }
        },
        async deleteEntry(entryId) {
            if ((await fetch(`/api/model-sets/${this.selectedSetId}/entries/${entryId}`, {method: 'DELETE'})).ok) {
                const s = this.selectedSet;
                if (s) s.entries = s.entries.filter(e => e.id !== entryId);
            }
        },
        async _reorderEntries() {
            const entries = this.selectedSet?.entries;
            if (!entries) return;
            const items = entries.map((e, i) => ({id: e.id, priority: i + 1}));
            await fetch(`/api/model-sets/${this.selectedSetId}/entries/reorder`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(items),
            });
        },
        async moveUpEntry(idx) {
            const entries = this.selectedSet?.entries;
            if (!entries || idx <= 0) return;
            const arr = [...entries];
            const t = arr[idx - 1].priority;
            arr[idx - 1].priority = arr[idx].priority;
            arr[idx].priority = t;
            arr.sort((a, b) => a.priority - b.priority);
            this.selectedSet.entries = arr;
            await this._reorderEntries();
        },
        async moveDownEntry(idx) {
            const entries = this.selectedSet?.entries;
            if (!entries || idx >= entries.length - 1) return;
            const arr = [...entries];
            const t = arr[idx + 1].priority;
            arr[idx + 1].priority = arr[idx].priority;
            arr[idx].priority = t;
            arr.sort((a, b) => a.priority - b.priority);
            this.selectedSet.entries = arr;
            await this._reorderEntries();
        },
    };
}

function logsData() {
    return {
        logs: [],
        grouped: [],
        loading: true,
        statusFilter: null,  // null = all, 'error' = errors only
        init() { this.fetchLogs(); },
        setFilter(filter) {
            this.statusFilter = filter;
            this.fetchLogs();
        },
        async fetchLogs() {
            try {
                let url = '/api/logs?limit=50';
                if (this.statusFilter) {
                    url += '&status=' + encodeURIComponent(this.statusFilter);
                }
                const resp = await fetch(url);
                this.logs = await resp.json();
                this.groupLogs();
            } catch (e) { console.error(e); }
            this.loading = false;
        },
        groupLogs() {
            const groups = {};
            for (const log of this.logs) {
                const key = log.request_id || 'nogroup';
                if (!groups[key]) groups[key] = [];
                groups[key].push(log);
            }
            // Sort each group by time ascending (failover → success chain)
            for (const key in groups) {
                groups[key].sort((a, b) => {
                    const ta = a.timestamp || '';
                    const tb = b.timestamp || '';
                    return ta < tb ? -1 : ta > tb ? 1 : 0;
                });
            }
            // Convert to array, sorted by most recent group
            this.grouped = Object.values(groups).sort((a, b) => {
                const ta = a[a.length - 1]?.timestamp || '';
                const tb = b[b.length - 1]?.timestamp || '';
                return ta < tb ? 1 : ta > tb ? -1 : 0;
            });
        },
        formatTime(ts) {
            if (!ts) return '—';
            const d = new Date(ts);
            return d.toLocaleTimeString();
        },
        statusStyle(status) {
            if (status === 'success') return 'color:#2fb344;font-weight:600;';
            if (status === 'failover') return 'color:#f5a623;font-weight:600;';
            return 'color:#ff5050;font-weight:600;';
        },
        formatTokens(log) {
            const parts = [];
            if (log.prompt_tokens != null) parts.push('↑' + log.prompt_tokens);
            if (log.completion_tokens != null) parts.push('↓' + log.completion_tokens);
            return parts.join(' ') || '—';
        },
        groupStatus(group) {
            const last = group[group.length - 1];
            return last ? last.status : 'unknown';
        },
    };
}

function dashboardData() {
    return {
        // Stats
        stats: null,
        statsLoading: true,
        // Logs
        logs: [],
        grouped: [],
        logLoading: true,
        logFilter: null,
        statsInterval: null,
        logInterval: null,

        init() {
            this.fetchStats();
            this.fetchLogs();
            this.statsInterval = setInterval(() => this.fetchStats(), 10000);
            this.logInterval = setInterval(() => this.fetchLogs(), 5000);
        },
        destroy() {
            if (this.statsInterval) clearInterval(this.statsInterval);
            if (this.logInterval) clearInterval(this.logInterval);
        },

        // ---- Stats ----
        async fetchStats() {
            try {
                const resp = await fetch('/api/stats');
                this.stats = await resp.json();
            } catch (e) { console.error(e); }
            this.statsLoading = false;
        },

        // ---- Logs ----
        setLogFilter(filter) {
            this.logFilter = filter;
            this.fetchLogs();
        },
        async fetchLogs() {
            try {
                let url = '/api/logs?limit=50';
                if (this.logFilter) url += '&status=' + encodeURIComponent(this.logFilter);
                const resp = await fetch(url);
                this.logs = await resp.json();
                this.groupLogs();
            } catch (e) { console.error(e); }
            this.logLoading = false;
        },
        groupLogs() {
            const groups = {};
            for (const log of this.logs) {
                const key = log.request_id || 'nogroup';
                if (!groups[key]) groups[key] = [];
                groups[key].push(log);
            }
            for (const key in groups) {
                groups[key].sort((a, b) => {
                    const ta = a.timestamp || '';
                    const tb = b.timestamp || '';
                    return ta < tb ? -1 : ta > tb ? 1 : 0;
                });
            }
            this.grouped = Object.values(groups).sort((a, b) => {
                const ta = a[a.length - 1]?.timestamp || '';
                const tb = b[b.length - 1]?.timestamp || '';
                return ta < tb ? 1 : ta > tb ? -1 : 0;
            });
        },
        formatTime(ts) {
            if (!ts) return '—';
            return new Date(ts).toLocaleTimeString();
        },
        statusStyle(status) {
            if (status === 'success') return 'color:#2fb344;font-weight:600;';
            if (status === 'failover') return 'color:#f5a623;font-weight:600;';
            return 'color:#ff5050;font-weight:600;';
        },
        formatTokens(log) {
            const parts = [];
            if (log.prompt_tokens != null) parts.push('↑' + log.prompt_tokens);
            if (log.completion_tokens != null) parts.push('↓' + log.completion_tokens);
            return parts.join(' ') || '—';
        },
    };
}

function settingsData() {
    return {
        settings: [],
        loading: true,
        saved: false,
        init() {
            this.fetchSettings();
        },
        async fetchSettings() {
            try {
                const resp = await fetch('/api/settings');
                this.settings = await resp.json();
            } catch (e) { console.error(e); }
            this.loading = false;
        },
        async saveAll() {
            for (const s of this.settings) {
                await fetch(`/api/settings/${encodeURIComponent(s.key)}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({value: s.value}),
                });
            }
            this.saved = true;
            setTimeout(() => this.saved = false, 3000);
        },
        settingLabel(key) {
            const labels = {
                circuit_breaker_threshold: 'Circuit Breaker Threshold',
                circuit_breaker_cooldown: 'Circuit Breaker Cooldown (seconds)',
                default_timeout: 'Default Timeout (seconds)',
                set_retry_limit: 'Set Retry Limit',
            };
            return labels[key] || key;
        },
        settingHelp(key) {
            const help = {
                circuit_breaker_threshold: 'Consecutive failures before a provider is skipped',
                circuit_breaker_cooldown: 'How long to skip a failing provider (seconds)',
                default_timeout: 'Default request timeout for new providers (seconds)',
                set_retry_limit: 'How many times to retry the entire set from the beginning when all entries fail',
            };
            return help[key] || '';
        },
        settingDefault(key) {
            const defaults = {
                circuit_breaker_threshold: '3',
                circuit_breaker_cooldown: '30',
                default_timeout: '60',
                set_retry_limit: '2',
            };
            return 'Default: ' + (defaults[key] || '');
        },
    };
}

function statsData() {
    return {
        stats: null,
        loading: true,
        init() { this.fetchStats(); },
        async fetchStats() {
            this.loading = true;
            try {
                const resp = await fetch('/api/stats');
                this.stats = await resp.json();
            } catch (e) { console.error('Failed to fetch stats:', e); }
            this.loading = false;
        },
    };
}
