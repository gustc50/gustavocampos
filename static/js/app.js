const App = {
    state: {
        certificate: null,
        activeCategory: null,
        nfe: { entrada: [], saida: [], eventos: [], ultimoNsu: '0', maxNsu: '0' },
        nfse: { prestados: [], tomados: [] },
    },

    init() {
        this.bindEvents();
        this.setupDragDrop();
    },

    bindEvents() {
        document.getElementById('certFile').addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file) {
                document.getElementById('fileName').textContent = file.name;
                document.getElementById('fileName').style.display = 'block';
            }
        });

        document.getElementById('btnUploadCert').addEventListener('click', () => this.uploadCertificate());

        document.querySelectorAll('.category-tab').forEach(tab => {
            tab.addEventListener('click', () => this.switchCategory(tab.dataset.category));
        });

        document.querySelectorAll('.sub-tab').forEach(tab => {
            tab.addEventListener('click', () => this.switchSubTab(tab));
        });

        document.getElementById('btnConsultarNfe').addEventListener('click', () => this.consultarNfe());
        document.getElementById('btnBuscarMaisNfe').addEventListener('click', () => this.consultarNfe(true));
        document.getElementById('btnConsultarNfsePrestados').addEventListener('click', () => this.consultarNfse('prestados'));
        document.getElementById('btnConsultarNfseTomados').addEventListener('click', () => this.consultarNfse('tomados'));

        document.getElementById('btnDownloadEntrada').addEventListener('click', () => this.downloadZip('entrada'));
        document.getElementById('btnDownloadSaida').addEventListener('click', () => this.downloadZip('saida'));
        document.getElementById('btnDownloadPrestados').addEventListener('click', () => this.downloadZip('prestados'));
        document.getElementById('btnDownloadTomados').addEventListener('click', () => this.downloadZip('tomados'));

        document.getElementById('btnLogout').addEventListener('click', () => this.logout());
    },

    setupDragDrop() {
        const area = document.getElementById('uploadArea');
        ['dragenter', 'dragover'].forEach(e => {
            area.addEventListener(e, (ev) => { ev.preventDefault(); area.classList.add('dragover'); });
        });
        ['dragleave', 'drop'].forEach(e => {
            area.addEventListener(e, (ev) => { ev.preventDefault(); area.classList.remove('dragover'); });
        });
        area.addEventListener('drop', (ev) => {
            const file = ev.dataTransfer.files[0];
            if (file) {
                document.getElementById('certFile').files = ev.dataTransfer.files;
                document.getElementById('fileName').textContent = file.name;
                document.getElementById('fileName').style.display = 'block';
            }
        });
    },

    async uploadCertificate() {
        const fileInput = document.getElementById('certFile');
        const password = document.getElementById('certPassword').value;

        if (!fileInput.files[0]) {
            this.showToast('Selecione o arquivo do certificado (.pfx ou .p12)', 'error');
            return;
        }
        if (!password) {
            this.showToast('Digite a senha do certificado', 'error');
            return;
        }

        const formData = new FormData();
        formData.append('certificate', fileInput.files[0]);
        formData.append('password', password);

        const btn = document.getElementById('btnUploadCert');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner" style="width:20px;height:20px;border-width:2px;display:inline-block;vertical-align:middle;margin-right:8px;"></span> Carregando...';

        try {
            const res = await fetch('/api/upload-certificate', { method: 'POST', body: formData });
            const data = await res.json();
            if (data.success) {
                this.state.certificate = data.certificate;
                this.showDashboard();
                this.showToast('Certificado carregado com sucesso!', 'success');
            } else {
                this.showToast(data.error, 'error');
            }
        } catch (err) {
            this.showToast('Erro ao conectar com o servidor', 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = 'Acessar';
        }
    },

    showDashboard() {
        const cert = this.state.certificate;
        document.getElementById('loginScreen').style.display = 'none';
        document.getElementById('dashboard').classList.add('active');

        document.getElementById('certTitular').textContent = cert.titular;
        document.getElementById('certCnpj').textContent = this.formatCnpjCpf(cert.cnpj_cpf);
        document.getElementById('certValidade').textContent = cert.validade;
        document.getElementById('certEmissor').textContent = cert.emissor;
        document.getElementById('headerCertName').textContent = cert.titular.substring(0, 30);

        if (cert.expirado) {
            document.getElementById('certValidadeStatus').innerHTML = '<span style="color:var(--danger)">EXPIRADO</span>';
        }
    },

    switchCategory(category) {
        this.state.activeCategory = category;
        document.querySelectorAll('.category-tab').forEach(t => t.classList.remove('active-nfe', 'active-nfse'));
        document.querySelector(`[data-category="${category}"]`).classList.add(category === 'nfe' ? 'active-nfe' : 'active-nfse');
        document.querySelectorAll('.section-panel').forEach(p => p.classList.remove('active'));
        document.getElementById(`section-${category}`).classList.add('active');
    },

    switchSubTab(tab) {
        const panel = tab.closest('.section-panel');
        panel.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        const target = tab.dataset.tab;
        panel.querySelectorAll('.sub-content').forEach(c => c.classList.remove('active'));
        panel.querySelector(`[data-content="${target}"]`).classList.add('active');
    },

    async consultarNfe(continuar = false) {
        const uf = document.getElementById('nfeUf').value;
        const nsuUltimo = continuar ? this.state.nfe.ultimoNsu : '0';

        if (!continuar) {
            this.state.nfe = { entrada: [], saida: [], eventos: [], ultimoNsu: '0', maxNsu: '0' };
        }

        this.showLoading('nfe', true);

        try {
            const res = await fetch('/api/consultar-nfe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uf, nsu_ultimo: nsuUltimo }),
            });
            const data = await res.json();

            if (data.success) {
                if (continuar) {
                    this.state.nfe.entrada.push(...data.entrada);
                    this.state.nfe.saida.push(...data.saida);
                    this.state.nfe.eventos.push(...data.eventos);
                } else {
                    this.state.nfe.entrada = data.entrada;
                    this.state.nfe.saida = data.saida;
                    this.state.nfe.eventos = data.eventos;
                }
                this.state.nfe.ultimoNsu = data.ultimo_nsu;
                this.state.nfe.maxNsu = data.max_nsu;
                this.renderNfeResults();
                this.showToast(`Consulta realizada! ${data.total_entrada} entrada(s), ${data.total_saida} saida(s)`, 'success');
            } else {
                this.showToast(data.error, 'error');
            }
        } catch (err) {
            this.showToast('Erro ao consultar SEFAZ: ' + err.message, 'error');
        } finally {
            this.showLoading('nfe', false);
        }
    },

    renderNfeResults() {
        const { entrada, saida, ultimoNsu, maxNsu } = this.state.nfe;

        document.getElementById('badgeEntrada').textContent = entrada.length;
        document.getElementById('badgeSaida').textContent = saida.length;

        document.getElementById('nfeSummaryEntrada').textContent = entrada.length;
        document.getElementById('nfeSummarySaida').textContent = saida.length;
        document.getElementById('nfeSummaryValorEntrada').textContent =
            this.formatCurrency(entrada.reduce((s, n) => s + parseFloat(n.valor_total || 0), 0));
        document.getElementById('nfeSummaryValorSaida').textContent =
            this.formatCurrency(saida.reduce((s, n) => s + parseFloat(n.valor_total || 0), 0));

        const hasMore = ultimoNsu !== maxNsu && maxNsu !== '0';
        document.getElementById('nsuInfo').textContent = `NSU: ${ultimoNsu} / ${maxNsu}`;
        document.getElementById('btnBuscarMaisNfe').style.display = hasMore ? 'inline-flex' : 'none';

        this.renderNfeTable('tableEntrada', entrada);
        this.renderNfeTable('tableSaida', saida);

        document.getElementById('emptyEntrada').style.display = entrada.length ? 'none' : 'block';
        document.getElementById('emptySaida').style.display = saida.length ? 'none' : 'block';
        document.getElementById('btnDownloadEntrada').style.display = entrada.length ? 'inline-flex' : 'none';
        document.getElementById('btnDownloadSaida').style.display = saida.length ? 'inline-flex' : 'none';
    },

    renderNfeTable(tableId, notas) {
        const tbody = document.querySelector(`#${tableId} tbody`);
        tbody.innerHTML = '';
        notas.forEach((nota, idx) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${nota.numero || nota.nsu || '-'}</td>
                <td>${nota.serie || '-'}</td>
                <td class="chave" title="${nota.chave || ''}">${(nota.chave || '').substring(0, 20)}...</td>
                <td>${nota.emitente_nome || nota.emitente_cnpj || '-'}</td>
                <td>${nota.destinatario_nome || nota.destinatario_cnpj || '-'}</td>
                <td>${this.formatDate(nota.data_emissao)}</td>
                <td>${nota.natureza || '-'}</td>
                <td class="valor">R$ ${this.formatNumber(nota.valor_total)}</td>
                <td>${nota.xml ? `<button class="btn btn-sm btn-outline" onclick="App.downloadSingleXml(${idx}, '${tableId}')">XML</button>` : ''}</td>
            `;
            tbody.appendChild(tr);
        });
    },

    async consultarNfse(tipo) {
        const cap = capitalize(tipo);
        const dataInicio = document.getElementById(`nfse${cap}DataInicio`).value;
        const dataFim = document.getElementById(`nfse${cap}DataFim`).value;
        const urlWs = document.getElementById(`nfse${cap}UrlWs`).value;
        const codMunicipio = document.getElementById(`nfse${cap}CodMunicipio`).value;

        if (!urlWs) { this.showToast('Informe a URL do webservice da prefeitura', 'error'); return; }
        if (!dataInicio || !dataFim) { this.showToast('Informe o periodo de consulta', 'error'); return; }

        this.showLoading('nfse', true);

        try {
            const res = await fetch('/api/consultar-nfse', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tipo, data_inicio: dataInicio, data_fim: dataFim, url_webservice: urlWs, codigo_municipio: codMunicipio }),
            });
            const data = await res.json();

            if (data.success) {
                this.state.nfse[tipo] = data.notas;
                this.renderNfseResults(tipo);
                this.showToast(`${data.total} NFSe ${tipo} encontrada(s)`, 'success');
            } else {
                this.showToast(data.error, 'error');
            }
        } catch (err) {
            this.showToast('Erro ao consultar: ' + err.message, 'error');
        } finally {
            this.showLoading('nfse', false);
        }
    },

    renderNfseResults(tipo) {
        const notas = this.state.nfse[tipo];
        const cap = capitalize(tipo);

        document.getElementById(`badge${cap}`).textContent = notas.length;
        document.getElementById(`nfseSummary${cap}`).textContent = notas.length;
        document.getElementById(`nfseSummaryValor${cap}`).textContent =
            this.formatCurrency(notas.reduce((s, n) => s + parseFloat(n.valor_servicos || 0), 0));

        const tbody = document.querySelector(`#table${cap} tbody`);
        tbody.innerHTML = '';
        notas.forEach((nota, idx) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${nota.numero || '-'}</td>
                <td>${tipo === 'prestados' ? (nota.tomador_nome || nota.tomador_cnpj || '-') : (nota.prestador_nome || nota.prestador_cnpj || '-')}</td>
                <td>${nota.descricao ? nota.descricao.substring(0, 50) + '...' : '-'}</td>
                <td>${this.formatDate(nota.data_emissao)}</td>
                <td>${nota.codigo_servico || '-'}</td>
                <td class="valor">R$ ${this.formatNumber(nota.valor_servicos)}</td>
                <td class="valor">R$ ${this.formatNumber(nota.valor_iss)}</td>
                <td>${nota.aliquota ? (parseFloat(nota.aliquota) * 100).toFixed(2) + '%' : '-'}</td>
                <td>${nota.xml ? `<button class="btn btn-sm btn-outline" onclick="App.downloadSingleNfseXml(${idx}, '${tipo}')">XML</button>` : ''}</td>
            `;
            tbody.appendChild(tr);
        });

        document.getElementById(`empty${cap}`).style.display = notas.length ? 'none' : 'block';
        document.getElementById(`btnDownload${cap}`).style.display = notas.length ? 'inline-flex' : 'none';
    },

    downloadSingleXml(idx, tableId) {
        let nota;
        if (tableId === 'tableEntrada') nota = this.state.nfe.entrada[idx];
        else if (tableId === 'tableSaida') nota = this.state.nfe.saida[idx];
        if (!nota || !nota.xml) return;
        const chave = nota.chave || nota.numero || `nota_${idx}`;
        this.triggerXmlDownload(nota.xml, `${chave}.xml`);
    },

    downloadSingleNfseXml(idx, tipo) {
        const nota = this.state.nfse[tipo][idx];
        if (!nota || !nota.xml) return;
        this.triggerXmlDownload(nota.xml, `nfse_${nota.numero || idx}.xml`);
    },

    triggerXmlDownload(xmlContent, filename) {
        const blob = new Blob([xmlContent], { type: 'application/xml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename; a.click();
        URL.revokeObjectURL(url);
    },

    async downloadZip(tipo) {
        const map = {
            entrada: { notas: this.state.nfe.entrada, filename: 'nfe_entrada.zip' },
            saida: { notas: this.state.nfe.saida, filename: 'nfe_saida.zip' },
            prestados: { notas: this.state.nfse.prestados, filename: 'nfse_prestados.zip' },
            tomados: { notas: this.state.nfse.tomados, filename: 'nfse_tomados.zip' },
        };
        const { notas, filename } = map[tipo];
        if (!notas || !notas.length) { this.showToast('Nenhuma nota para download', 'error'); return; }

        try {
            const res = await fetch('/api/download-zip', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ notas, filename }),
            });
            if (res.ok) {
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url; a.download = filename; a.click();
                URL.revokeObjectURL(url);
                this.showToast(`Download de ${notas.length} nota(s) iniciado`, 'success');
            } else {
                this.showToast('Erro ao gerar arquivo ZIP', 'error');
            }
        } catch (err) {
            this.showToast('Erro no download: ' + err.message, 'error');
        }
    },

    async logout() {
        try { await fetch('/api/logout', { method: 'POST' }); } catch (e) {}
        this.state = { certificate: null, activeCategory: null, nfe: { entrada: [], saida: [], eventos: [], ultimoNsu: '0', maxNsu: '0' }, nfse: { prestados: [], tomados: [] } };
        document.getElementById('dashboard').classList.remove('active');
        document.getElementById('loginScreen').style.display = 'flex';
        document.getElementById('certFile').value = '';
        document.getElementById('certPassword').value = '';
        document.getElementById('fileName').style.display = 'none';
        document.querySelectorAll('.category-tab').forEach(t => t.classList.remove('active-nfe', 'active-nfse'));
        document.querySelectorAll('.section-panel').forEach(p => p.classList.remove('active'));
    },

    showLoading(section, show) {
        const el = document.getElementById(`loading-${section}`);
        if (el) el.classList.toggle('active', show);
    },

    showToast(message, type = 'info') {
        const container = document.getElementById('toastContainer');
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        const icon = type === 'error' ? '&#10060;' : type === 'success' ? '&#9989;' : '&#8505;&#65039;';
        toast.innerHTML = `<span>${icon}</span><span>${message}</span>`;
        container.appendChild(toast);
        setTimeout(() => toast.remove(), 5000);
    },

    formatCnpjCpf(v) {
        if (!v) return '';
        if (v.length === 14) return v.replace(/(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})/, '$1.$2.$3/$4-$5');
        if (v.length === 11) return v.replace(/(\d{3})(\d{3})(\d{3})(\d{2})/, '$1.$2.$3-$4');
        return v;
    },

    formatCurrency(v) { return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v); },
    formatNumber(v) { if (!v) return '0,00'; return parseFloat(v).toLocaleString('pt-BR', { minimumFractionDigits: 2 }); },
    formatDate(d) {
        if (!d) return '-';
        if (d.includes('T')) d = d.split('T')[0];
        const p = d.split('-');
        if (p.length === 3) return `${p[2]}/${p[1]}/${p[0]}`;
        return d;
    },
};

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
document.addEventListener('DOMContentLoaded', () => App.init());
