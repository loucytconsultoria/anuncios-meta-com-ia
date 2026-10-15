// Orquestração com Claude Code (ferramenta Workflow): três verificadores independentes.
//
// Cada bloco é dado a N verificadores (padrão três) que não veem a resposta um do outro e que
// NÃO escreveram o material. O consenso é calculado depois por consenso.py, que manda para
// revisão humana tudo o que não tiver maioria ou tiver qualquer divergência.
//
// args:
//   modo           "checagem": cada frase com número do relatório é recalculada contra a base SQLite
//                  "rotulo":   cada item recebe um rótulo de uma lista fechada, com justificativa
//   pasta          pasta com os blocos (bloco_NN.json) e onde os resultados são gravados
//   blocos         lista de nomes de bloco (ex.: ["bloco_01", "bloco_02"])
//   banco          (checagem) caminho do SQLite
//   convencoes     (checagem) texto com as convenções do relatório que NÃO são erro (arredondamento etc.)
//   rotulos        (rotulo) lista de rótulos permitidos, ex.: ["sustenta", "nao_sustenta", "insuficiente"]
//   pergunta       (rotulo) a pergunta que cada verificador responde sobre o item
//   verificadores  padrão ["v1", "v2", "v3"]

export const meta = {
  name: 'verificar-tres',
  description: 'Verificadores independentes conferem cada item (frase com número ou rótulo) e gravam o veredito',
  whenToUse: 'Antes de publicar números ou classificações sensíveis. Depois: consenso.py.',
  phases: [{ title: 'Verificação', detail: 'N verificadores por bloco, em paralelo' }],
}

const P = args.pasta

function promptChecagem(bloco, v) {
  return `Conferência independente de números de um relatório. Você NÃO escreveu o texto: procure erro.
Execute agora; não responda com status nem com perguntas. Toda mensagem sua é uma chamada de ferramenta até gravar o arquivo.

1. Leia o bloco de frases: ${P}/${bloco}.json (cada item tem "id", "secao" e "frase").
2. Para cada frase, confira TODO número contra a base SQLite ${args.banco}, consultando com python (sqlite3).
   Prefira recalcular da base a confiar em qualquer arquivo de resumo.
3. Convenções do relatório (não são erro):
${args.convencoes || '   - diferença de arredondamento de até 1 unidade não é erro; percentuais com uma casa decimal.'}
4. Grave UM arquivo JSON Lines em ${P}/resultados/${bloco}_${v}.jsonl, um objeto por frase, na ordem do bloco, com:
   - "id"; "veredito": "CONFERE" | "DIVERGE" | "NAO_VERIFICAVEL"
   - "valor_na_base": o que você apurou (texto curto) ou ""; "consulta": a SQL ou o cálculo usado, em uma linha
   - "observacao": até 25 palavras; em DIVERGE, diga o número certo
   Frase sem como conferir (opinião, recomendação) é NAO_VERIFICAVEL.
5. Resposta final: só "OK <número de linhas gravadas>".`
}

function promptRotulo(bloco, v) {
  return `Verificação independente. Outros verificadores respondem a mesma pergunta sem ver a sua resposta.
Execute agora; toda mensagem sua é uma chamada de ferramenta até gravar o arquivo.

1. Leia ${P}/${bloco}.json (cada item tem "id" e o material a avaliar).
2. Pergunta: ${args.pergunta}
   Responda SOMENTE com um destes rótulos: ${(args.rotulos || []).join(' | ')}.
   Julgue só pelo material do item; não use conhecimento externo sobre quem é o autor.
3. Grave ${P}/${v}_${bloco}.jsonl, um objeto por item, na ordem do bloco: {"id", "rotulo", "justificativa"}
   (justificativa: até 30 palavras, citando o trecho literal que decide).
4. Resposta final: só "OK <número de linhas gravadas>".`
}

phase('Verificação')
const verificadores = args.verificadores || ['v1', 'v2', 'v3']
const tarefas = []
for (const b of args.blocos) for (const v of verificadores) tarefas.push({ b, v })
log(`${tarefas.length} tarefas (${args.blocos.length} blocos x ${verificadores.length} verificadores)`)
const res = await parallel(tarefas.map((t) => () =>
  agent(args.modo === 'rotulo' ? promptRotulo(t.b, t.v) : promptChecagem(t.b, t.v),
        { label: `${args.modo}:${t.b}:${t.v}`, phase: 'Verificação', model: args.modelo || 'sonnet', effort: 'low' })))
const ok = res.filter((r) => typeof r === 'string' && r.trim().startsWith('OK')).length
return { tarefas: tarefas.length, ok, falhas: tarefas.length - ok }
