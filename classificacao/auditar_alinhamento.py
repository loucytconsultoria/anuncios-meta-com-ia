# -*- coding: utf-8 -*-
"""Auditoria do alinhamento imagem -> resultado, pelas transcrições dos subagentes.

A falha que isto pega: um leitor abre duas imagens parecidas seguidas antes de gravar a
primeira e passa a gravar o resultado de uma com o conteúdo da outra. O texto gravado parece
correto (é o texto de UMA das imagens), então nenhuma validação de formato percebe.

Protocolo de leitura (ver workflows/ler_lotes.js): uma imagem aberta por mensagem, e a mensagem
seguinte grava o resultado no arquivo com o NOME DA IMAGEM aberta. Aqui, para cada gravação de
resultado, conferimos no registro de ferramentas da transcrição qual foi a última imagem aberta
ANTES daquela mensagem. Se não for a imagem daquele arquivo, o resultado é desalinhado: com
--aplicar ele é apagado e o lote volta para a fila na validação.

Os vereditos ficam num registro persistente, então as transcrições podem ser apagadas depois
(--limpar-minutos) sem perder a auditoria: cada imagem lida deixa centenas de KB nelas.

Formato esperado da transcrição (JSON Lines do Claude Code): mensagens com
  {"message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read"|"Write",
               "input": {"file_path": "..."}}]}}

Uso:
  python -m classificacao.auditar_alinhamento --trabalho dados/leitura --transcricoes "caminho/*/agent-*.jsonl" [--aplicar] [--limpar-minutos 15]
"""
import argparse
import sys
import glob
import json
import os
import re
import time

EXT_IMG = ('.jpg', '.jpeg', '.png', '.webp')
RX_RES = re.compile(r'resultados_(img|qa)[\\/]([^\\/]+)[\\/]([^\\/]+)\.json$', re.I)


def _stem(p):
    return os.path.splitext(os.path.basename(p.replace('\\', '/')))[0]


def manifestos(trab):
    """(sub, lote, nome_do_arquivo_de_resultado) -> nome da imagem esperada."""
    m = {}
    for sub in ('img', 'qa'):
        for f in glob.glob(os.path.join(trab, 'lotes_' + sub, '*.json')):
            lote = os.path.basename(f)[:-5]
            with open(f, encoding='utf-8') as fh:
                for it in json.load(fh)['itens']:
                    st = _stem(it['caminho'])
                    m[(sub, lote, st)] = st
    return m


def auditar_transcricao(caminho, man, res):
    ultima = None                       # nome da última imagem aberta em mensagens ANTERIORES
    with open(caminho, encoding='utf-8', errors='replace') as fh:
        for linha in fh:
            try:
                d = json.loads(linha)
            except Exception:
                continue
            msg = d.get('message') or {}
            if msg.get('role') != 'assistant':
                continue
            lidas_aqui = []
            for c in msg.get('content') or []:
                if not isinstance(c, dict) or c.get('type') != 'tool_use':
                    continue
                fp = (c.get('input') or {}).get('file_path', '') or ''
                if c.get('name') == 'Read' and fp.lower().endswith(EXT_IMG):
                    lidas_aqui.append(_stem(fp))
                elif c.get('name') == 'Write':
                    m = RX_RES.search(fp)
                    if not m:
                        continue
                    chave = (m.group(1), m.group(2), m.group(3))
                    esperado = man.get(chave)
                    # Read e Write na mesma mensagem também é desalinhado: 'ultima' ainda é a anterior
                    res['|'.join(chave)] = {'ok': esperado is not None and esperado == ultima,
                                            'esperado': esperado, 'ultima_aberta': ultima,
                                            'transcricao': os.path.basename(caminho)}
            if lidas_aqui:
                ultima = lidas_aqui[-1]


def auditar(trab, padrao_transcricoes, aplicar=False, limpar_minutos=None):
    man = manifestos(trab)
    reg_path = os.path.join(trab, '_auditoria_registro.json')
    res = json.load(open(reg_path, encoding='utf-8')) if os.path.exists(reg_path) else {}
    # da mais antiga para a mais nova: se um item foi relido, vale o veredito da releitura
    transcricoes = sorted(glob.glob(padrao_transcricoes), key=os.path.getmtime) if padrao_transcricoes else []
    for f in transcricoes:
        auditar_transcricao(f, man, res)
    with open(reg_path, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False)
    apagadas = 0
    if limpar_minutos is not None:
        agora = time.time()
        for f in transcricoes:
            if agora - os.path.getmtime(f) > limpar_minutos * 60:
                os.remove(f)
                apagadas += 1

    def existe(k):
        s, l, n = k.split('|')
        return os.path.exists(os.path.join(trab, 'resultados_' + s, l, n + '.json'))

    ruins = [k for k, v in res.items() if not v['ok'] and existe(k)]
    sem = []
    for sub in ('img', 'qa'):
        for p in glob.glob(os.path.join(trab, 'resultados_' + sub, '*', '*.json')):
            k = '%s|%s|%s' % (sub, os.path.basename(os.path.dirname(p)), os.path.basename(p)[:-5])
            if k not in res:
                sem.append(k)
    if aplicar:
        for k in ruins:
            s, l, n = k.split('|')
            os.remove(os.path.join(trab, 'resultados_' + s, l, n + '.json'))
            j = os.path.join(trab, 'resultados_' + s, l + '.jsonl')
            if os.path.exists(j):                       # lote já consolidado: desfaz para refazer
                os.replace(j, j + '.desalinhado')
    out = {'gravacoes_auditadas': len(res), 'desalinhadas': len(ruins),
           'lotes_afetados': sorted({k.split('|')[1] for k in ruins}), 'sem_auditoria': len(sem),
           'aplicado': aplicar, 'transcricoes_apagadas': apagadas,
           'exemplos': [dict(id=k, **res[k]) for k in ruins[:10]]}
    with open(os.path.join(trab, '_auditoria.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--trabalho', required=True)
    ap.add_argument('--transcricoes', required=True, help='padrão glob das transcrições dos subagentes')
    ap.add_argument('--aplicar', action='store_true', help='apaga os resultados desalinhados')
    ap.add_argument('--limpar-minutos', type=float, default=None,
                    help='apaga transcrições mais velhas que N minutos depois de auditar')
    a = ap.parse_args()
    out = auditar(a.trabalho, a.transcricoes, a.aplicar, a.limpar_minutos)
    print(json.dumps({k: v for k, v in out.items() if k != 'exemplos'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
