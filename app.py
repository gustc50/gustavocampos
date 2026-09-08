"""
Aplicativo para download de Notas Fiscais via Certificado Digital.
- NFe: Notas de Entrada e Saida (SEFAZ - DistribuicaoDFe)
- NFSe: Servicos Prestados e Tomados (Padrao ABRASF)
"""

import os
import io
import json
import zipfile
import tempfile
import base64
import gzip
import re
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify,
    send_file, session
)
from OpenSSL import crypto
from lxml import etree
import requests

app = Flask(__name__)
app.secret_key = os.urandom(32)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['DOWNLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['DOWNLOAD_FOLDER'], exist_ok=True)

NS_NFE = 'http://www.portalfiscal.inf.br/nfe'
NS_DIST = 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe'
NS_SOAP = 'http://www.w3.org/2003/05/soap-envelope'

SEFAZ_URLS = {
    'distribuicao': 'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx',
}

UF_CODES = {
    'AC': '12', 'AL': '27', 'AP': '16', 'AM': '13', 'BA': '29',
    'CE': '23', 'DF': '53', 'ES': '32', 'GO': '52', 'MA': '21',
    'MT': '51', 'MS': '50', 'MG': '31', 'PA': '15', 'PB': '25',
    'PR': '41', 'PE': '26', 'PI': '22', 'RJ': '33', 'RN': '24',
    'RS': '43', 'RO': '11', 'RR': '14', 'SC': '42', 'SP': '35',
    'SE': '28', 'TO': '17',
}


def load_certificate(pfx_path, password):
    with open(pfx_path, 'rb') as f:
        pfx_data = f.read()

    p12 = crypto.load_pkcs12(pfx_data, password.encode('utf-8'))
    cert = p12.get_certificate()
    key = p12.get_privatekey()

    cert_pem = crypto.dump_certificate(crypto.FILETYPE_PEM, cert)
    key_pem = crypto.dump_privatekey(crypto.FILETYPE_PEM, key)

    subject = cert.get_subject()
    issuer = cert.get_issuer()
    not_after = datetime.strptime(
        cert.get_notAfter().decode('ascii'), '%Y%m%d%H%M%SZ'
    )

    cert_info = {
        'titular': subject.CN or '',
        'cnpj_cpf': extract_cnpj_cpf(subject.CN or ''),
        'emissor': issuer.CN or '',
        'validade': not_after.strftime('%d/%m/%Y %H:%M'),
        'expirado': datetime.now() > not_after,
        'serial': str(cert.get_serial_number()),
    }

    return cert_pem, key_pem, cert_info


def extract_cnpj_cpf(cn):
    match = re.search(r':(\d{11,14})', cn)
    if match:
        return match.group(1)
    digits = re.findall(r'\d+', cn)
    for d in digits:
        if len(d) in (11, 14):
            return d
    return ''


def create_temp_cert_files(cert_pem, key_pem):
    cert_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pem')
    cert_file.write(cert_pem)
    cert_file.write(key_pem)
    cert_file.close()
    return cert_file.name


def build_dist_dfe_xml(uf_code, cnpj, nsu_ultimo='0', cons_nsu=None):
    nsu_ultimo = str(nsu_ultimo).zfill(15)

    if cons_nsu:
        cons_nsu = str(cons_nsu).zfill(15)
        body = f'''<consNSU xmlns="{NS_NFE}" versao="1.01">
            <NSU>{cons_nsu}</NSU>
        </consNSU>'''
    else:
        body = f'''<distNSU xmlns="{NS_NFE}" versao="1.01">
            <ultNSU>{nsu_ultimo}</ultNSU>
        </distNSU>'''

    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <soap12:Envelope xmlns:soap12="{NS_SOAP}"
                     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                     xmlns:xsd="http://www.w3.org/2001/XMLSchema">
        <soap12:Header/>
        <soap12:Body>
            <nfeDistDFeInteresse xmlns="{NS_DIST}">
                <nfeDadosMsg>
                    <distDFeInt xmlns="{NS_NFE}" versao="1.01">
                        <tpAmb>1</tpAmb>
                        <cUFAutor>{uf_code}</cUFAutor>
                        <CNPJ>{cnpj}</CNPJ>
                        {body}
                    </distDFeInt>
                </nfeDadosMsg>
            </nfeDistDFeInteresse>
        </soap12:Body>
    </soap12:Envelope>'''


def query_sefaz_distribuicao(cert_file_path, uf_code, cnpj, nsu_ultimo='0'):
    xml_request = build_dist_dfe_xml(uf_code, cnpj, nsu_ultimo)
    headers = {'Content-Type': 'application/soap+xml; charset=utf-8'}

    response = requests.post(
        SEFAZ_URLS['distribuicao'],
        data=xml_request.encode('utf-8'),
        headers=headers,
        cert=cert_file_path,
        verify=True,
        timeout=60,
    )
    return response


def parse_dist_dfe_response(xml_response):
    root = etree.fromstring(xml_response)
    ns = {'soap': NS_SOAP, 'nfe': NS_NFE}

    ret = root.find('.//nfe:retDistDFeInt', ns)
    if ret is None:
        return {'status': 'erro', 'message': 'Resposta invalida do SEFAZ', 'notas': []}

    c_stat = ret.findtext('nfe:cStat', '', ns)
    x_motivo = ret.findtext('nfe:xMotivo', '', ns)
    ult_nsu = ret.findtext('nfe:ultNSU', '0', ns)
    max_nsu = ret.findtext('nfe:maxNSU', '0', ns)

    notas = []
    for doc in ret.findall('.//nfe:docZip', ns):
        nsu = doc.get('NSU', '')
        schema = doc.get('schema', '')
        xml_b64 = doc.text

        if xml_b64:
            try:
                xml_compressed = base64.b64decode(xml_b64)
                xml_content = gzip.decompress(xml_compressed)
                nota_info = parse_nota_xml(xml_content, nsu, schema)
                if nota_info:
                    notas.append(nota_info)
            except Exception as e:
                notas.append({'nsu': nsu, 'schema': schema, 'erro': str(e)})

    return {
        'status': c_stat, 'motivo': x_motivo,
        'ultimo_nsu': ult_nsu, 'max_nsu': max_nsu, 'notas': notas,
    }


def parse_nota_xml(xml_bytes, nsu, schema):
    try:
        root = etree.fromstring(xml_bytes)
    except Exception:
        return None

    ns = {'nfe': NS_NFE}
    tag = etree.QName(root).localname

    nota = {
        'nsu': nsu, 'schema': schema,
        'tipo_documento': tag,
        'xml': xml_bytes.decode('utf-8', errors='replace'),
    }

    if tag in ('nfeProc', 'NFe'):
        inf_nfe = root.find('.//nfe:infNFe', ns)
        if inf_nfe is not None:
            nota['chave'] = inf_nfe.get('Id', '').replace('NFe', '')

        emit = root.find('.//nfe:emit', ns)
        dest = root.find('.//nfe:dest', ns)
        ide = root.find('.//nfe:ide', ns)
        icms_tot = root.find('.//nfe:ICMSTot', ns)

        if ide is not None:
            nota['numero'] = ide.findtext('nfe:nNF', '', ns)
            nota['serie'] = ide.findtext('nfe:serie', '', ns)
            dh = ide.findtext('nfe:dhEmi', '', ns)
            nota['data_emissao'] = dh[:10] if dh else ''
            nota['tipo_operacao'] = ide.findtext('nfe:tpNF', '', ns)
            nota['natureza'] = ide.findtext('nfe:natOp', '', ns)

        if emit is not None:
            nota['emitente_cnpj'] = emit.findtext('nfe:CNPJ', '', ns)
            nota['emitente_nome'] = emit.findtext('nfe:xNome', '', ns)

        if dest is not None:
            nota['destinatario_cnpj'] = dest.findtext(
                'nfe:CNPJ', dest.findtext('nfe:CPF', '', ns), ns
            )
            nota['destinatario_nome'] = dest.findtext('nfe:xNome', '', ns)

        if icms_tot is not None:
            nota['valor_total'] = icms_tot.findtext('nfe:vNF', '0.00', ns)

    elif tag == 'resNFe':
        nota['chave'] = root.findtext('nfe:chNFe', '', ns)
        nota['emitente_cnpj'] = root.findtext('nfe:CNPJ', '', ns)
        nota['emitente_nome'] = root.findtext('nfe:xNome', '', ns)
        dh = root.findtext('nfe:dhEmi', '', ns)
        nota['data_emissao'] = dh[:10] if dh else ''
        nota['tipo_operacao'] = root.findtext('nfe:tpNF', '', ns)
        nota['valor_total'] = root.findtext('nfe:vNF', '0.00', ns)
        nota['situacao'] = root.findtext('nfe:cSitNFe', '', ns)

    elif tag == 'resEvento':
        nota['chave'] = root.findtext('nfe:chNFe', '', ns)
        nota['tipo_evento'] = root.findtext('nfe:tpEvento', '', ns)
        nota['descricao_evento'] = root.findtext('nfe:xEvento', '', ns)
        dh = root.findtext('nfe:dhEvento', '', ns)
        nota['data_evento'] = dh[:10] if dh else ''

    return nota


def classify_nfe(nota, cnpj_empresa):
    tp_nf = nota.get('tipo_operacao', '')
    emit_cnpj = nota.get('emitente_cnpj', '')

    if tp_nf == '0':
        return 'entrada'
    elif tp_nf == '1':
        return 'saida' if emit_cnpj == cnpj_empresa else 'entrada'
    else:
        return 'saida' if emit_cnpj == cnpj_empresa else 'entrada'


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/upload-certificate', methods=['POST'])
def upload_certificate():
    if 'certificate' not in request.files:
        return jsonify({'success': False, 'error': 'Nenhum arquivo enviado'}), 400

    file = request.files['certificate']
    password = request.form.get('password', '')

    if not file.filename:
        return jsonify({'success': False, 'error': 'Nenhum arquivo selecionado'}), 400

    if not file.filename.lower().endswith(('.pfx', '.p12')):
        return jsonify({'success': False, 'error': 'Arquivo deve ser .pfx ou .p12'}), 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'cert.pfx')
    file.save(filepath)

    try:
        cert_pem, key_pem, cert_info = load_certificate(filepath, password)
        cert_file = create_temp_cert_files(cert_pem, key_pem)

        session['cert_file'] = cert_file
        session['cert_info'] = cert_info
        session['pfx_path'] = filepath
        session['pfx_password'] = password

        return jsonify({'success': True, 'certificate': cert_info})

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Erro ao carregar certificado: {str(e)}',
        }), 400


@app.route('/api/certificate-info')
def certificate_info():
    cert_info = session.get('cert_info')
    if not cert_info:
        return jsonify({'success': False, 'error': 'Nenhum certificado carregado'}), 400
    return jsonify({'success': True, 'certificate': cert_info})


@app.route('/api/consultar-nfe', methods=['POST'])
def consultar_nfe():
    cert_file = session.get('cert_file')
    cert_info = session.get('cert_info')

    if not cert_file or not cert_info:
        return jsonify({'success': False, 'error': 'Certificado nao carregado'}), 400

    data = request.get_json() or {}
    uf = data.get('uf', 'SP')
    nsu_ultimo = data.get('nsu_ultimo', '0')

    uf_code = UF_CODES.get(uf, '35')
    cnpj = cert_info.get('cnpj_cpf', '')

    if not cnpj or len(cnpj) != 14:
        return jsonify({
            'success': False,
            'error': 'CNPJ nao encontrado no certificado. Verifique se e um e-CNPJ.',
        }), 400

    try:
        response = query_sefaz_distribuicao(cert_file, uf_code, cnpj, nsu_ultimo)

        if response.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'Erro HTTP {response.status_code} do SEFAZ',
            }), 502

        result = parse_dist_dfe_response(response.content)

        entrada, saida, eventos = [], [], []
        for nota in result.get('notas', []):
            if nota.get('tipo_documento') == 'resEvento':
                eventos.append(nota)
            else:
                classificacao = classify_nfe(nota, cnpj)
                if classificacao == 'entrada':
                    entrada.append(nota)
                else:
                    saida.append(nota)

        return jsonify({
            'success': True,
            'status': result.get('status'),
            'motivo': result.get('motivo'),
            'ultimo_nsu': result.get('ultimo_nsu'),
            'max_nsu': result.get('max_nsu'),
            'entrada': entrada, 'saida': saida, 'eventos': eventos,
            'total_entrada': len(entrada), 'total_saida': len(saida),
            'total_eventos': len(eventos),
        })

    except requests.exceptions.SSLError as e:
        return jsonify({
            'success': False,
            'error': f'Erro SSL: {str(e)}. Verifique se o certificado e valido.',
        }), 400
    except requests.exceptions.ConnectionError:
        return jsonify({
            'success': False,
            'error': 'Erro de conexao com o SEFAZ. Tente novamente.',
        }), 502
    except Exception as e:
        return jsonify({
            'success': False, 'error': f'Erro na consulta: {str(e)}',
        }), 500


@app.route('/api/consultar-nfse', methods=['POST'])
def consultar_nfse():
    cert_file = session.get('cert_file')
    cert_info = session.get('cert_info')

    if not cert_file or not cert_info:
        return jsonify({'success': False, 'error': 'Certificado nao carregado'}), 400

    data = request.get_json() or {}
    tipo = data.get('tipo', 'prestados')
    data_inicio = data.get('data_inicio', '')
    data_fim = data.get('data_fim', '')
    codigo_municipio = data.get('codigo_municipio', '')
    url_webservice = data.get('url_webservice', '')

    cnpj = cert_info.get('cnpj_cpf', '')

    if not url_webservice:
        return jsonify({
            'success': False,
            'error': 'URL do webservice da prefeitura e obrigatoria. '
                     'Cada municipio possui seu proprio webservice de NFSe.',
        }), 400

    try:
        if tipo == 'prestados':
            xml_request = build_nfse_prestados_xml(cnpj, data_inicio, data_fim)
        else:
            xml_request = build_nfse_tomados_xml(cnpj, data_inicio, data_fim)

        headers = {'Content-Type': 'text/xml; charset=utf-8'}

        response = requests.post(
            url_webservice,
            data=xml_request.encode('utf-8'),
            headers=headers,
            cert=cert_file,
            verify=True,
            timeout=60,
        )

        if response.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'Erro HTTP {response.status_code} do webservice',
            }), 502

        notas = parse_nfse_response(response.content, tipo)

        return jsonify({
            'success': True, 'tipo': tipo,
            'notas': notas, 'total': len(notas),
        })

    except requests.exceptions.SSLError as e:
        return jsonify({'success': False, 'error': f'Erro SSL: {str(e)}'}), 400
    except requests.exceptions.ConnectionError:
        return jsonify({
            'success': False,
            'error': 'Erro de conexao com o webservice da prefeitura.',
        }), 502
    except Exception as e:
        return jsonify({
            'success': False, 'error': f'Erro na consulta: {str(e)}',
        }), 500


def build_nfse_prestados_xml(cnpj, data_inicio, data_fim):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <ConsultarNfseServicoPrestadoEnvio xmlns="http://www.abrasf.org.br/nfse.xsd">
        <Prestador>
            <CpfCnpj><Cnpj>{cnpj}</Cnpj></CpfCnpj>
        </Prestador>
        <PeriodoEmissao>
            <DataInicial>{data_inicio}</DataInicial>
            <DataFinal>{data_fim}</DataFinal>
        </PeriodoEmissao>
        <Pagina>1</Pagina>
    </ConsultarNfseServicoPrestadoEnvio>'''


def build_nfse_tomados_xml(cnpj, data_inicio, data_fim):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <ConsultarNfseServicoTomadoEnvio xmlns="http://www.abrasf.org.br/nfse.xsd">
        <Consulente>
            <CpfCnpj><Cnpj>{cnpj}</Cnpj></CpfCnpj>
        </Consulente>
        <PeriodoEmissao>
            <DataInicial>{data_inicio}</DataInicial>
            <DataFinal>{data_fim}</DataFinal>
        </PeriodoEmissao>
        <Pagina>1</Pagina>
    </ConsultarNfseServicoTomadoEnvio>'''


def parse_nfse_response(xml_bytes, tipo):
    notas = []
    try:
        root = etree.fromstring(xml_bytes)
        ns = {'nfse': 'http://www.abrasf.org.br/nfse.xsd'}

        for comp in root.findall('.//nfse:CompNfse', ns):
            nfse = comp.find('nfse:Nfse', ns)
            if nfse is None:
                continue

            inf = nfse.find('.//nfse:InfNfse', ns)
            if inf is None:
                continue

            nota = {
                'numero': inf.findtext('nfse:Numero', '', ns),
                'codigo_verificacao': inf.findtext('nfse:CodigoVerificacao', '', ns),
                'data_emissao': inf.findtext('nfse:DataEmissao', '', ns),
                'competencia': inf.findtext('nfse:Competencia', '', ns),
            }

            servico = inf.find('.//nfse:Servico', ns)
            if servico is not None:
                valores = servico.find('nfse:Valores', ns)
                if valores is not None:
                    nota['valor_servicos'] = valores.findtext('nfse:ValorServicos', '0.00', ns)
                    nota['valor_iss'] = valores.findtext('nfse:ValorIss', '0.00', ns)
                    nota['aliquota'] = valores.findtext('nfse:Aliquota', '0.00', ns)
                nota['descricao'] = servico.findtext('nfse:Discriminacao', '', ns)
                nota['codigo_servico'] = servico.findtext('nfse:ItemListaServico', '', ns)

            prestador = inf.find('.//nfse:PrestadorServico', ns)
            if prestador is not None:
                nota['prestador_nome'] = prestador.findtext('.//nfse:RazaoSocial', '', ns)
                cpf_cnpj = prestador.find('.//nfse:CpfCnpj', ns)
                if cpf_cnpj is not None:
                    nota['prestador_cnpj'] = cpf_cnpj.findtext('nfse:Cnpj', '', ns)

            tomador = inf.find('.//nfse:TomadorServico', ns)
            if tomador is not None:
                nota['tomador_nome'] = tomador.findtext('.//nfse:RazaoSocial', '', ns)
                cpf_cnpj = tomador.find('.//nfse:CpfCnpj', ns)
                if cpf_cnpj is not None:
                    nota['tomador_cnpj'] = cpf_cnpj.findtext('nfse:Cnpj', '', ns)

            nota['tipo'] = tipo
            nota['xml'] = etree.tostring(comp, encoding='unicode')
            notas.append(nota)

    except Exception:
        pass

    return notas


@app.route('/api/download-xml', methods=['POST'])
def download_xml():
    data = request.get_json() or {}
    xml_content = data.get('xml', '')
    filename = data.get('filename', 'nota.xml')

    if not xml_content:
        return jsonify({'success': False, 'error': 'XML nao fornecido'}), 400

    buffer = io.BytesIO(xml_content.encode('utf-8'))
    buffer.seek(0)
    return send_file(buffer, mimetype='application/xml', as_attachment=True, download_name=filename)


@app.route('/api/download-zip', methods=['POST'])
def download_zip():
    data = request.get_json() or {}
    notas = data.get('notas', [])
    filename = data.get('filename', 'notas.zip')

    if not notas:
        return jsonify({'success': False, 'error': 'Nenhuma nota para download'}), 400

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, nota in enumerate(notas):
            xml = nota.get('xml', '')
            chave = nota.get('chave', nota.get('numero', f'nota_{i+1}'))
            zf.writestr(f'{chave}.xml', xml)

    buffer.seek(0)
    return send_file(buffer, mimetype='application/zip', as_attachment=True, download_name=filename)


@app.route('/api/logout', methods=['POST'])
def logout():
    cert_file = session.get('cert_file')
    if cert_file and os.path.exists(cert_file):
        os.unlink(cert_file)

    pfx_path = session.get('pfx_path')
    if pfx_path and os.path.exists(pfx_path):
        os.unlink(pfx_path)

    session.clear()
    return jsonify({'success': True})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
