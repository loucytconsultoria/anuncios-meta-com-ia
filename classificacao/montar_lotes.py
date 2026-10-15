# -*- coding: utf-8 -*-
"""Monta os lotes de leitura para os subagentes (incremental e deduplicado).

Unidade de leitura:
  imagem  imagem DISTINTA. Duas cópias da mesma arte (recodificada, redimensionada) ficam a poucos
          bits de distância no hash perceptual (dHash de 256 bits) e são lidas uma vez só. Em anúncios, a mesma arte roda
          em dezenas de variações: deduplicar é o que torna a leitura em escala viável.
  texto   texto distinto (texto + título normalizados), global entre entidades.

Ordem: as imagens mais vistas (soma do peso, ex.: impressões) entram primeiro, para que um corte
de orçamento no meio do caminho ainda cubra o que mais pesa.

Só entra em lote novo o que ainda não tem resultado válido nem está num lote pendente.
Amostra de controle: ~2% das imagens e dos textos novos vão TAMBÉM para lotes de releitura
(lotes_qa / lotes_qatxt), lidos por outro subagente que não vê a primeira leitura.

Entrada: manifesto JSON Lines, um item por linha:
  {"id": "...", "imagens": ["caminho.png", ...], "texto": "...", "titulo": "...", "peso": 1234}
Saídas (na pasta de trabalho): lotes_img/, lotes_txt/, lotes_qa/, lotes_qatxt/, _mapa_itens.json,
  _novos_lotes.json (lista de lotes para passar ao workflow)

Uso: python -m classificacao.montar_lotes --manifesto itens.jsonl --trabalho dados/leitura [--tam-img 12] [--tam-txt 50]
"""
import argparse
import sys
import glob
import hashlib
import json
import os
import random
import re
import unicodedata
from collections import defaultdict

try:
    from PIL import Image
except ImportError:          # sem Pillow: deduplica só cópias idênticas byte a byte
    Image = None


def norm_txt(x):
    x = unicodedata.normalize('NFKD', x or '').encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.sub(r'[^a-z0-9 ]', ' ', x).split())


def txt_id(texto, titulo):
    return hashlib.sha1((norm_txt(texto) + '|' + norm_txt(titulo)).encode()).hexdigest()[:16]


def hash_imagem(caminho, n=16):
    """dHash de n*n bits (diferença entre pixels vizinhos numa miniatura em cinza), em hexadecimal."""
    if Image is None:
        with open(caminho, 'rb') as f:
            return 'sha1_' + hashlib.sha1(f.read()).hexdigest()
    with Image.open(caminho) as im:
        px = im.convert('L').resize((n + 1, n), Image.LANCZOS).tobytes()
    bits = 0
    for y in range(n):
        for x in range(n):
            bits = (bits << 1) | (1 if px[y * (n + 1) + x] > px[y * (n + 1) + x + 1] else 0)
    return '%0*x' % (n * n // 4, bits)


def distancia(h1, h2):
    if h1.startswith('sha1_') or h2.startswith('sha1_'):
        return 0 if h1 == h2 else 10 ** 6
    return bin(int(h1, 16) ^ int(h2, 16)).count('1')


class Agrupador:
    """Agrupa imagens quase iguais: mesma arte recodificada ou redimensionada fica a poucos bits
    de distância (nos testes, até ~10 de 256); artes diferentes ficam bem mais longe."""
    def __init__(self, limiar=12, conhecidos=()):
        self.limiar = limiar
        self.reps = list(conhecidos)

    def chave(self, h):
        for r in self.reps:
            if r == h or distancia(r, h) <= self.limiar:
                return r
        self.reps.append(h)
        return h


def _ja_resolvidos(trab, sub, chave):
    ok, pend = set(), set()
    for f in glob.glob(os.path.join(trab, 'resultados_' + sub, '*.jsonl')):
        with open(f, encoding='utf-8', errors='replace') as fh:
            for linha in fh:
                try:
                    d = json.loads(linha)
                    if not d.get('erro'):
                        ok.add(d[chave])
                except Exception:
                    pass
    for f in glob.glob(os.path.join(trab, 'lotes_' + sub, '*.json')):
        nome = os.path.basename(f).replace('.json', '.jsonl')
        if not os.path.exists(os.path.join(trab, 'resultados_' + sub, nome)):
            with open(f, encoding='utf-8') as fh:
                pend |= {x[chave] for x in json.load(fh)['itens']}
    return ok, pend


def _proximo(trab, sub, prefixo):
    ns = [int(re.findall(r'(\d+)', os.path.basename(f))[0])
          for f in glob.glob(os.path.join(trab, 'lotes_' + sub, prefixo + '_*.json'))]
    return (max(ns) + 1) if ns else 1


def _gravar_lotes(trab, sub, prefixo, itens, tam):
    n0 = _proximo(trab, sub, prefixo)
    nomes = []
    for k in range(0, len(itens), tam):
        nome = '%s_%05d' % (prefixo, n0 + k // tam)
        with open(os.path.join(trab, 'lotes_' + sub, nome + '.json'), 'w', encoding='utf-8') as f:
            json.dump({'lote': nome, 'itens': itens[k:k + tam]}, f, ensure_ascii=False, indent=0)
        nomes.append(nome)
    return nomes


def montar(manifesto, trab, tam_img=12, tam_txt=50, pct_qa=0.02, semente=None, limiar=12):
    for d in ('lotes_img', 'lotes_txt', 'lotes_qa', 'lotes_qatxt',
              'resultados_img', 'resultados_txt', 'resultados_qa', 'resultados_qatxt'):
        os.makedirs(os.path.join(trab, d), exist_ok=True)
    base = os.path.dirname(os.path.abspath(manifesto))
    itens = []
    with open(manifesto, encoding='utf-8') as f:
        for linha in f:
            if linha.strip():
                itens.append(json.loads(linha))

    # ------------------------------------------------ imagens distintas
    repr_de, peso, mapa = {}, defaultdict(float), {}
    # imagens já lidas em rodadas anteriores continuam sendo os representantes do grupo
    ok_antes, pend_antes = _ja_resolvidos(trab, 'img', 'img')
    agrup = Agrupador(limiar, sorted(ok_antes | pend_antes))
    for it in itens:
        hs = []
        for c in it.get('imagens') or []:
            cam = c if os.path.isabs(c) else os.path.join(base, c)
            h = agrup.chave(hash_imagem(cam))
            repr_de.setdefault(h, os.path.abspath(cam))
            peso[h] += float(it.get('peso') or 0)
            hs.append(h)
        tid = txt_id(it.get('texto'), it.get('titulo')) if (it.get('texto') or it.get('titulo') or '').strip() else None
        mapa[it['id']] = {'imagens': hs, 'texto': tid, 'peso': it.get('peso')}
    ok, pend = _ja_resolvidos(trab, 'img', 'img')
    novas = [h for h in sorted(repr_de, key=lambda h: (-peso[h], h)) if h not in ok and h not in pend]
    lotes_img = _gravar_lotes(trab, 'img', 'img', [{'img': h, 'caminho': repr_de[h]} for h in novas], tam_img)

    # ------------------------------------------------ textos distintos
    textos = {}
    for it in itens:
        tid = mapa[it['id']]['texto']
        if tid and tid not in textos:
            textos[tid] = {'txt': tid, 'texto': it.get('texto') or '', 'titulo': it.get('titulo') or ''}
    ok, pend = _ja_resolvidos(trab, 'txt', 'txt')
    novos_t = [t for t in textos if t not in ok and t not in pend]
    lotes_txt = _gravar_lotes(trab, 'txt', 'txt', [textos[t] for t in novos_t], tam_txt)

    # ------------------------------------------------ amostra de releitura independente
    rnd = random.Random(semente if semente is not None else len(novas) * 7919 + len(repr_de))
    k_img = min(len(novas), max(1 if novas else 0, round(len(novas) * pct_qa)))
    am = rnd.sample(novas, k_img)
    lotes_qa = _gravar_lotes(trab, 'qa', 'qa', [{'img': h, 'caminho': repr_de[h]} for h in am], 10)
    k_txt = min(len(novos_t), max(1 if novos_t else 0, round(len(novos_t) * pct_qa)))
    amt = rnd.sample(novos_t, k_txt)
    lotes_qatxt = _gravar_lotes(trab, 'qatxt', 'qatxt', [textos[t] for t in amt], 25)

    # mapa item -> unidades lidas (para juntar os resultados de volta aos anúncios)
    caminho_mapa = os.path.join(trab, '_mapa_itens.json')
    antigo = json.load(open(caminho_mapa, encoding='utf-8')) if os.path.exists(caminho_mapa) else {}
    antigo.update(mapa)
    with open(caminho_mapa, 'w', encoding='utf-8') as f:
        json.dump(antigo, f, ensure_ascii=False)

    res = {'img': lotes_img, 'txt': lotes_txt, 'qa': lotes_qa, 'qatxt': lotes_qatxt,
           'totais': {'itens': len(itens), 'imagens_referenciadas': sum(len(m['imagens']) for m in mapa.values()),
                      'imagens_distintas': len(repr_de), 'imagens_novas': len(novas),
                      'textos_distintos': len(textos), 'textos_novos': len(novos_t),
                      'amostra_qa_img': len(am), 'amostra_qa_txt': len(amt)}}
    with open(os.path.join(trab, '_novos_lotes.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False)
    return res


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')   # console do Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--manifesto', required=True)
    ap.add_argument('--trabalho', required=True)
    ap.add_argument('--tam-img', type=int, default=12)
    ap.add_argument('--tam-txt', type=int, default=50)
    ap.add_argument('--pct-qa', type=float, default=0.02)
    ap.add_argument('--limiar-hamming', type=int, default=12, help='bits de diferença (de 256) para considerar a mesma imagem')
    a = ap.parse_args()
    res = montar(a.manifesto, a.trabalho, a.tam_img, a.tam_txt, a.pct_qa, limiar=a.limiar_hamming)
    print(json.dumps(res['totais'], ensure_ascii=False))
    print('lotes: ' + ' '.join('%s=%d' % (k, len(v)) for k, v in res.items() if k != 'totais'))


if __name__ == '__main__':
    main()
