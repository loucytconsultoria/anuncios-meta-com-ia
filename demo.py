# -*- coding: utf-8 -*-
"""Demonstração offline das três partes, com os dados sintéticos de exemplos/. Não usa token.

  1. Coleta      deduplicação entre arquivos de coleta, atribuição por documento/nome/página,
                 descarte de homônimos e de anúncios pagos por terceiros, agregação por entidade.
  2. Leitura     lotes deduplicados por hash perceptual, uma "onda" de leitura SIMULADA (o que os
                 subagentes gravariam), com um erro de alinhamento proposital que a auditoria pega,
                 validação contra o vocabulário fechado, concordância com a releitura de controle,
                 consenso de três verificadores e relatório gerado por script.
  3. Marketing   estrutura de campanhas numa conta SIMULADA: aplicar sem validar é recusado,
                 validate_only gera o recibo, a subida sobrevive a uma queda de rede sem duplicar,
                 a segunda execução não cria nada, a prova pega uma edição manual e a auditoria
                 pós-subida aponta os anúncios fora das regras.

Uso: python demo.py [--saida saida_demo]
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from classificacao import auditar_alinhamento, concordancia, consenso, frases_para_conferir, montar_lotes, relatorio, validar_lotes  # noqa: E402
from coleta.amostra_auditoria import gerar as gerar_amostra  # noqa: E402
from coleta.coletar import ler_entidades  # noqa: E402
from coleta.consolidar import consolidar  # noqa: E402
from marketing_api import auditar_anuncios, subir_estrutura  # noqa: E402
from marketing_api.simulado import ClienteSimulado  # noqa: E402

EX = os.path.join(AQUI, 'exemplos')
VOC = os.path.join(EX, 'vocabularios', 'politicas_publicas.json')


def titulo(t):
    print('\n' + '=' * 78 + '\n' + t + '\n' + '=' * 78)


def brl(v):
    return ('R$ {:,.0f}'.format(v or 0)).replace(',', '.')


# ============================================================================ 1. coleta
def parte_coleta(saida):
    titulo('1. COLETA: deduplicação, atribuição e agregação (Biblioteca de Anúncios, dados sintéticos)')
    banco = os.path.join(saida, 'banco')
    resumo, ents = consolidar(ler_entidades(os.path.join(EX, 'entidades.csv')), os.path.join(EX, 'coleta'), banco,
                              regiao='Meridiano', inicio='2026-08-01', corte='2026-09-30', verbose=False)
    print('linhas lidas nos arquivos de coleta: %d -> anúncios únicos: %d (%d repetições removidas; vale a versão mais nova)'
          % (resumo['linhas_lidas'], resumo['anuncios_unicos'], resumo['duplicatas_removidas']))
    print('atribuídos: %d | por vínculo: %s' % (resumo['atribuidos'], json.dumps(resumo['por_vinculo'], ensure_ascii=False)))
    print('não atribuídos: %s' % json.dumps(resumo['nao_atribuidos'], ensure_ascii=False))
    print('em página de entidade mas pagos por outro (fora dos totais): %d' % resumo['pagos_por_outro_fora_dos_totais'])
    print('CPM de referência (anúncios de R$ 100+) para imputar a faixa R$ 0-99: %s' % resumo['cpm_referencia'])
    print('\n%-36s %4s  %-21s %10s  %s' % ('entidade', 'n', 'gasto piso-teto', 'estimado', 'impressões piso-teto'))
    for e in ents:
        if e['n_anuncios']:
            print('%-36s %4d  %-21s %10s  %s-%s' % (e['entidade'][:36], e['n_anuncios'],
                                                   '%s-%s' % (brl(e['gasto_piso']), brl(e['gasto_teto'])),
                                                   brl(e['gasto_estimado']), e['impressoes_piso'], e['impressoes_teto']))
    g = resumo['gasto_total']
    print('total: gasto entre %s e %s (estimado %s)' % (brl(g['piso']), brl(g['teto']), brl(g['estimado'])))
    n, _ = gerar_amostra(os.path.join(banco, 'base_anuncios.sqlite'), os.path.join(saida, 'amostra_auditoria_atribuicao.md'), 5)
    print('amostra para auditoria independente da atribuição: %d linhas -> amostra_auditoria_atribuicao.md' % n)
    return os.path.join(banco, 'base_anuncios.sqlite')


# ============================================================================ 2. leitura
def _manifesto(banco, saida):
    with open(os.path.join(EX, 'criativos.json'), encoding='utf-8') as f:
        criativos = json.load(f)
    db = sqlite3.connect(banco)
    cam = os.path.join(saida, 'manifesto_criativos.jsonl')
    with open(cam, 'w', encoding='utf-8') as f:
        for ad, texto, tit, imp in db.execute('SELECT ad_id, texto, titulo_link, impressoes_meio FROM anuncios'):
            imgs = [os.path.join(EX, 'imagens', x) for x in criativos.get(ad, [])]
            f.write(json.dumps({'id': ad, 'imagens': imgs, 'texto': texto or '', 'titulo': tit or '', 'peso': imp or 0},
                               ensure_ascii=False) + '\n')
    db.close()
    return cam


def _onda_simulada(trab, transcricao, errar=False):
    """Grava o que os leitores gravariam e a transcrição das ferramentas, no protocolo um a um.
    Com errar=True, o leitor do primeiro lote abre duas imagens seguidas e grava a primeira com o
    conteúdo da segunda: o erro que só a auditoria pela transcrição detecta."""
    with open(os.path.join(EX, 'leituras', 'imagens_principal.json'), encoding='utf-8') as f:
        princ = json.load(f)
    with open(os.path.join(EX, 'leituras', 'imagens_releitura.json'), encoding='utf-8') as f:
        rel = json.load(f)
    with open(os.path.join(EX, 'leituras', 'textos_principal.json'), encoding='utf-8') as f:
        txt = json.load(f)
    linhas = []

    def msg(nome, caminho):
        linhas.append({'message': {'role': 'assistant', 'content': [{'type': 'tool_use', 'name': nome, 'input': {'file_path': caminho}}]}})

    def gravar_img(sub, lote, stem, conteudo_de):
        base = conteudo_de.replace('_grande', '')
        d = dict((princ if sub == 'img' else rel)[base], arquivo=stem)
        dest = os.path.join(trab, 'resultados_' + sub, lote, stem + '.json')
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
        msg('Write', dest)

    pend = validar_lotes.validar(trab, VOC)[0] if os.path.exists(os.path.join(trab, '_pendentes.json')) else None
    for sub in ('img', 'qa'):
        for f in sorted(os.listdir(os.path.join(trab, 'lotes_' + sub))):
            lote = f[:-5]
            if pend is not None and lote not in pend[sub]:
                continue
            with open(os.path.join(trab, 'lotes_' + sub, f), encoding='utf-8') as fh:
                itens = json.load(fh)['itens']
            stems = [os.path.splitext(os.path.basename(x['caminho']))[0] for x in itens]
            k = 0
            while k < len(itens):
                if errar and sub == 'img' and k == 0 and len(itens) >= 2:
                    msg('Read', itens[0]['caminho'])
                    msg('Read', itens[1]['caminho'])                 # abriu a segunda sem gravar a primeira
                    gravar_img(sub, lote, stems[0], stems[1])        # e gravou a primeira com o conteúdo da segunda
                    gravar_img(sub, lote, stems[1], stems[1])
                    k = 2
                    errar = False
                    continue
                msg('Read', itens[k]['caminho'])
                gravar_img(sub, lote, stems[k], stems[k])
                k += 1
    for sub in ('txt', 'qatxt'):
        for f in sorted(os.listdir(os.path.join(trab, 'lotes_' + sub))):
            lote = f[:-5]
            dest = os.path.join(trab, 'resultados_' + sub, lote + '.jsonl')
            if os.path.exists(dest):
                continue
            with open(os.path.join(trab, 'lotes_' + sub, f), encoding='utf-8') as fh:
                itens = json.load(fh)['itens']
            with open(dest, 'w', encoding='utf-8') as fh:
                for j, it in enumerate(itens):
                    d = dict(txt[it['txt']])
                    if sub == 'qatxt' and j == 0:
                        d['tom'] = 'convocacao_mobilizacao'          # a releitura discordou do tom
                    fh.write(json.dumps(d, ensure_ascii=False) + '\n')
    os.makedirs(os.path.dirname(transcricao), exist_ok=True)
    with open(transcricao, 'w', encoding='utf-8') as f:
        for l in linhas:
            f.write(json.dumps(l, ensure_ascii=False) + '\n')


def parte_leitura(banco, saida):
    titulo('2. LEITURA COM LLM EM LOTE AUDITADO (onda de leitura simulada)')
    trab = os.path.join(saida, 'leitura')
    res = montar_lotes.montar(_manifesto(banco, saida), trab, tam_img=4, tam_txt=6, pct_qa=0.3, semente=4)
    t = res['totais']
    print('lotes: %d anúncios no manifesto, %d imagens referenciadas -> %d imagens distintas (hash perceptual); '
          '%d textos distintos' % (t['itens'], t['imagens_referenciadas'], t['imagens_distintas'], t['textos_distintos']))
    print('       lotes de imagem %s | de texto %s | releitura de controle %s + %s'
          % (len(res['img']), len(res['txt']), len(res['qa']), len(res['qatxt'])))

    trans = os.path.join(saida, 'transcricoes')
    _onda_simulada(trab, os.path.join(trans, 'onda1', 'agent-onda1.jsonl'), errar=True)
    aud = auditar_alinhamento.auditar(trab, os.path.join(trans, '*', 'agent-*.jsonl'), aplicar=True)
    print('\nauditoria de alinhamento (onda 1): %d gravações conferidas, %d DESALINHADA(S) -> apagada(s); exemplo: %s'
          % (aud['gravacoes_auditadas'], aud['desalinhadas'],
             {k: aud['exemplos'][0][k] for k in ('id', 'esperado', 'ultima_aberta')} if aud['exemplos'] else '-'))
    pend, st = validar_lotes.validar(trab, VOC)
    print('validação após a auditoria: lotes pendentes %s' % {k: v for k, v in pend.items() if v})
    _onda_simulada(trab, os.path.join(trans, 'onda2', 'agent-onda2.jsonl'))
    aud = auditar_alinhamento.auditar(trab, os.path.join(trans, '*', 'agent-*.jsonl'), aplicar=True)
    print('releitura do lote devolvido + nova auditoria: %d gravações, %d desalinhadas' % (aud['gravacoes_auditadas'], aud['desalinhadas']))
    pend, st = validar_lotes.validar(trab, VOC)
    print('validação final: pendentes %s | códigos fora do vocabulário convertidos em "outro": %d'
          % (sum(len(v) for v in pend.values()), sum(s['codigos_corrigidos'] for s in st.values())))

    q = concordancia.medir(trab, VOC)
    qi, qt = q['imagens'], q['textos']
    print('releitura de controle, imagens: %d itens, F1 médio das palavras %s, Jaccard de temas %s, tom igual %s%%'
          % (qi['comparados'], qi.get('f1_palavras_media'), qi.get('jaccard_temas_medio'), qi.get('pct_tom_igual')))
    print('releitura de controle, textos:  %d itens, Jaccard de temas %s, tom igual %s%%'
          % (qt['comparados'], qt.get('jaccard_temas_medio'), qt.get('pct_tom_igual')))

    ver = os.path.join(trab, 'verificacao')
    os.makedirs(ver, exist_ok=True)
    for nome in os.listdir(os.path.join(EX, 'verificacao')):          # copia só o conteúdo, sem atributos
        shutil.copyfile(os.path.join(EX, 'verificacao', nome), os.path.join(ver, nome))
    c = consenso.consenso_rotulo(ver, 3)
    print('consenso de 3 verificadores: %s; para revisão humana: %s'
          % (c['resumo'], [i for i, d in c['decisoes'].items() if d['revisao_humana']]))

    rel = relatorio.escrever(banco, trab, VOC, os.path.join(saida, 'relatorio_criativos.md'))
    frases, blocos = frases_para_conferir.extrair(rel, os.path.join(saida, 'checagem'), 15)
    print('relatório gerado por script: %s (%d frases com número separadas em %d bloco(s) para a checagem de 3 verificadores)'
          % (os.path.relpath(rel, AQUI), len(frases), len(blocos)))


# ============================================================================ 3. marketing api
def parte_marketing(saida):
    titulo('3. MARKETING API: estrutura idempotente numa conta SIMULADA')
    pasta = os.path.join(saida, 'subida')
    os.makedirs(pasta, exist_ok=True)
    extras = {'PAGINA': '0', 'REGIAO': '0', 'LINK_SITE': 'https://www.example.com/obras', 'IMAGEM_1': 'h1',
              'IMAGEM_2': 'h2', 'IMAGEM_3': 'h3', 'VIDEO_1': '0'}
    est, h, faltam = subir_estrutura.carregar_estrutura(os.path.join(EX, 'estrutura_exemplo.json'), extras)
    recibo, registro, conta = (os.path.join(pasta, x) for x in ('recibo_validacao.json', 'registro.json', 'conta_simulada.json'))
    for p in (recibo, registro, conta):
        if os.path.exists(p):
            os.remove(p)

    print('-- simulação offline (checagem local + plano)')
    subir_estrutura.simular(est, faltam)
    print('\n-- aplicar SEM validar antes:')
    subir_estrutura.aplicar(ClienteSimulado(arquivo=conta), est, h, faltam, recibo, registro)
    print('\n-- validar (validate_only, nada é criado):')
    subir_estrutura.validar(ClienteSimulado(arquivo=conta), est, h, faltam, recibo)
    print('\n-- aplicar, com queda de rede simulada depois que o 2º objeto já foi gravado na conta:')
    cli = ClienteSimulado(arquivo=conta, falhar_rede_no_post=2)
    r1 = subir_estrutura.aplicar(cli, est, h, faltam, recibo, registro)
    print('\n-- aplicar de novo (idempotência):')
    r2 = subir_estrutura.aplicar(ClienteSimulado(arquivo=conta), est, h, faltam, recibo, registro)
    print('\n   1ª execução criou %s; 2ª execução criou %s' % (r1, r2))
    print('\n-- prova (conta x estrutura):')
    subir_estrutura.prova(ClienteSimulado(arquivo=conta), est)
    # alguém edita a conta à mão: muda a verba de um conjunto e o texto de um anúncio
    with open(conta, encoding='utf-8') as f:
        objs = json.load(f)
    for a in objs['adsets'].values():
        if a['name'] == 'Tráfego · Site · 25-44':
            a['daily_budget'] = 9000
    for a in objs['ads'].values():
        if a['name'].startswith('EX-CARD-004'):
            a['creative'] = json.loads(json.dumps(a['creative']))
            a['creative']['object_story_spec']['link_data']['message'] = 'Veja as obras. Anúncio pago por Exemplo Ltda. (editado à mão)'
    with open(conta, 'w', encoding='utf-8') as f:
        json.dump(objs, f, ensure_ascii=False)
    print('\n-- prova depois de uma edição manual na conta:')
    subir_estrutura.prova(ClienteSimulado(arquivo=conta), est)
    print('\n-- auditoria pós-subida de CADA anúncio:')
    with open(os.path.join(EX, 'regras_auditoria.json'), encoding='utf-8') as f:
        regras = json.load(f)
    out, cont = auditar_anuncios.auditar(auditar_anuncios.ler_anuncios(ClienteSimulado(arquivo=conta)), est, regras)
    print('anúncios lidos: %d | com problema: %d' % (len(out), sum(1 for o in out if o['problemas'])))
    for o in out:
        if o['problemas']:
            print('  - %s: %s' % (o['nome'], '; '.join(o['problemas'])))


def _tirar_somente_leitura(func, caminho, _info):
    import stat
    os.chmod(caminho, stat.S_IWRITE)
    func(caminho)


def _limpar(pasta):
    """Apaga a saída anterior. Pastas sincronizadas com a nuvem às vezes seguram um
    diretório por alguns instantes: tenta de novo antes de desistir."""
    for tentativa in range(6):
        if not os.path.exists(pasta):
            return
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(pasta, onexc=_tirar_somente_leitura)
            else:
                shutil.rmtree(pasta, onerror=_tirar_somente_leitura)
            return
        except PermissionError:
            time.sleep(1 + tentativa)
    raise SystemExit('não consegui apagar %s; feche o que estiver usando a pasta ou use --saida em outro lugar' % pasta)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--saida', default=os.path.join(AQUI, 'saida_demo'))
    a = ap.parse_args()
    _limpar(a.saida)
    os.makedirs(a.saida, exist_ok=True)
    banco = parte_coleta(a.saida)
    parte_leitura(banco, a.saida)
    parte_marketing(a.saida)
    print('\nArquivos gerados em %s' % a.saida)


if __name__ == '__main__':
    main()
