"""
i18n.py — Internacionalização PT-BR / EN.

Uso:
    from i18n import t, set_lang, get_lang
    t("copy.title")                 → "COPIAR CANAL"
    t("auth.code", n=1)             → "Código de verificação (tentativa 1/3)"

Chaves ausentes retornam a própria chave (nunca quebram a interface).
"""

from __future__ import annotations

from typing import Dict, Tuple

_STRINGS: Dict[str, Dict[str, str]] = {}


def _s(key: str, pt: str, en: str) -> None:
    _STRINGS[key] = {"pt": pt, "en": en}


# ── Geral ─────────────────────────────────────────────────────────────────────
_s("app.tagline", "Replicador de Canais Telegram", "Telegram Channel Replicator")
_s("app.tech", "SQLite WAL  ·  AES-256-GCM  ·  Bloom Filter  ·  Async",
   "SQLite WAL  ·  AES-256-GCM  ·  Bloom Filter  ·  Async")
_s("app.by", "por", "by")
_s("app.ready", "Pronto", "Ready")

# ── Idioma ────────────────────────────────────────────────────────────────────
_s("lang.title", "IDIOMA / LANGUAGE", "LANGUAGE / IDIOMA")
_s("lang.welcome", "Bem-vindo! / Welcome!", "Welcome! / Bem-vindo!")
_s("lang.select", "Selecione o idioma / Select language", "Select language / Selecione o idioma")
_s("lang.pt", "Português (Brasil)", "Portuguese (Brazil)")
_s("lang.en", "English", "English")
_s("lang.current", "Idioma atual", "Current language")
_s("lang.set", "Idioma atualizado.", "Language updated.")

# ── Onboarding ────────────────────────────────────────────────────────────────
_s("onboard.title", "BEM-VINDO AO TELECOPY", "WELCOME TO TELECOPY")
_s("onboard.subtitle", "3 passos rápidos e você já está copiando canais",
   "3 quick steps and you're copying channels")
_s("onboard.step1.title", "Obtenha as credenciais do Telegram", "Get your Telegram credentials")
_s("onboard.step1.body",
   "Acesse https://my.telegram.org/apps, entre com sua conta, clique em 'API development tools' e crie um App. Copie o API ID (número) e o API Hash (32 caracteres).",
   "Go to https://my.telegram.org/apps, sign in, click 'API development tools' and create an App. Copy the API ID (number) and API Hash (32 characters).")
_s("onboard.step2.title", "Cole as credenciais no TELECOPY", "Paste the credentials into TELECOPY")
_s("onboard.step2.body",
   "Na tela seguinte informe API ID e API Hash. Eles são gravados localmente com AES-256-GCM e nunca saem do seu computador.",
   "On the next screen enter the API ID and API Hash. They are stored locally with AES-256-GCM and never leave your computer.")
_s("onboard.step3.title", "Faça login uma única vez", "Sign in once")
_s("onboard.step3.body",
   "Informe seu telefone com DDI (+55 para Brasil). O Telegram envia um código no app — digite aqui. Se houver senha de dois fatores, ela será pedida em seguida. Depois disso a sessão fica salva.",
   "Enter your phone with country code (+1 for USA). Telegram sends a code in the app — type it here. If 2-step verification is on, you'll be asked for the password. The session is saved afterwards.")
_s("onboard.tip", "Dica: para copiar você precisa apenas SER MEMBRO da origem e ADMINISTRADOR no destino.",
   "Tip: to copy you only need to be a MEMBER of the source and an ADMIN of the destination.")
_s("onboard.continue", "Pressione ENTER para configurar agora", "Press ENTER to set up now")

# ── Autenticação ──────────────────────────────────────────────────────────────
_s("auth.first_time.title", "CONFIGURAÇÃO INICIAL", "INITIAL SETUP")
_s("auth.first_time.subtitle", "Credenciais criptografadas com AES-256-GCM e armazenadas só no seu PC.",
   "Credentials encrypted with AES-256-GCM and stored only on your PC.")
_s("auth.creds_guide.title", "Como obter suas credenciais:", "How to get your credentials:")
_s("auth.creds_guide.steps",
   "Acesse https://my.telegram.org/apps|Faça login com seu número de telefone|Clique em 'API development tools'|Crie um App (qualquer nome)|Copie 'App api_id' e 'App api_hash'",
   "Go to https://my.telegram.org/apps|Sign in with your phone number|Click 'API development tools'|Create an App (any name)|Copy 'App api_id' and 'App api_hash'")
_s("auth.api_id", "API ID (somente números)", "API ID (numbers only)")
_s("auth.api_id_invalid", "API ID inválido: informe apenas números.", "Invalid API ID: numbers only.")
_s("auth.api_hash", "API Hash (32 caracteres)", "API Hash (32 characters)")
_s("auth.api_hash_invalid", "API Hash inválido: deve ter 32 caracteres.", "Invalid API Hash: must be 32 characters.")
_s("auth.creds_saved", "Credenciais salvas com criptografia AES-256-GCM.", "Credentials saved with AES-256-GCM.")
_s("auth.conn_failed", "Não foi possível conectar ao Telegram", "Could not connect to Telegram")
_s("auth.connecting", "Conectando ao Telegram…", "Connecting to Telegram…")
_s("auth.conn_hint", "Verifique sua internet, firewall ou proxy e tente novamente.",
   "Check your internet connection, firewall or proxy and try again.")
_s("auth.try_again", "Tentar conectar novamente?", "Try connecting again?")
_s("auth.conn_timeout", "tempo esgotado — rede bloqueando a conexão",
   "timed out — the network is blocking the connection")
_s("auth.signed_in", "Conectado como", "Signed in as")
_s("auth.session_invalid", "Sessão inválida ou expirada — será necessário entrar novamente.",
   "Session invalid or expired — you'll need to sign in again.")
_s("auth.sign_in.title", "VERIFICAÇÃO DE CONTA", "ACCOUNT VERIFICATION")
_s("auth.sign_in.subtitle", "O código chega no próprio app do Telegram.",
   "The code arrives inside the Telegram app.")
_s("auth.phone", "Telefone com DDI (ex.: +5511999999999)", "Phone with country code (e.g. +15550000000)")
_s("auth.phone_invalid", "Informe o telefone com o código do país (ex.: +5511999999999).",
   "Enter the phone with country code (e.g. +15550000000).")
_s("auth.code_send_fail", "Não foi possível enviar o código", "Could not send the code")
_s("auth.code_hint", "Abra o Telegram → chat 'Telegram' e copie o código.", "Open Telegram → 'Telegram' chat and copy the code.")
_s("auth.code", "Código (tentativa {n}/3)", "Code (attempt {n}/3)")
_s("auth.code_wrong", "Código incorreto. Tente novamente.", "Wrong code. Try again.")
_s("auth.code_expired", "Código expirado — enviando um novo…", "Code expired — sending a new one…")
_s("auth.2fa_hint", "Sua conta tem verificação em duas etapas (2FA).", "Your account has two-step verification (2FA).")
_s("auth.2fa", "Senha da verificação em duas etapas", "Two-step verification password")
_s("auth.2fa_fail", "Senha 2FA incorreta", "Wrong 2FA password")
_s("auth.too_many", "Muitas tentativas. Encerrando por segurança.", "Too many attempts. Exiting for safety.")
_s("auth.fail", "Falha na autenticação.", "Authentication failed.")
_s("auth.flood", "O Telegram pediu uma pausa de {sec}s antes de tentar novamente.",
   "Telegram asked to wait {sec}s before trying again.")

# ── Menu ──────────────────────────────────────────────────────────────────────
_s("menu.copy", "Copiar canal", "Copy channel")
_s("menu.copy.desc", "Clonar todo o histórico de um canal", "Clone a channel's full history")
_s("menu.multi", "Multi-destino", "Multi-destination")
_s("menu.multi.desc", "Copiar para vários canais de uma vez", "Copy to several channels at once")
_s("menu.mirror", "Modo espelho", "Mirror mode")
_s("menu.mirror.desc", "Sincronizar novas mensagens em tempo real", "Sync new messages in real time")
_s("menu.tools", "Ferramentas", "Tools")
_s("menu.tools.desc", "Diagnóstico, estatísticas e manutenção", "Diagnostics, statistics and maintenance")
_s("menu.settings", "Configurações", "Settings")
_s("menu.settings.desc", "Preferências, idioma e segurança", "Preferences, language and security")
_s("menu.exit", "Sair", "Exit")
_s("menu.back", "Voltar", "Back")
_s("menu.option", "Opção", "Option")
_s("menu.invalid", "Opção inválida.", "Invalid option.")
_s("menu.footer", "ENTER para confirmar  ·  0 para voltar", "ENTER to confirm  ·  0 to go back")

# ── Onboarding do seletor ─────────────────────────────────────────────────────
_s("picker.title", "SELECIONAR CANAL", "SELECT CHANNEL")
_s("picker.loading", "Carregando seus canais e grupos", "Loading your channels and groups")
_s("picker.found", "canais encontrados", "channels found")
_s("picker.none", "Nenhum canal ou grupo encontrado nesta conta.",
   "No channels or groups found in this account.")
_s("picker.page", "página", "page")
_s("picker.prev", "anterior", "previous")
_s("picker.next", "próxima", "next")
_s("picker.search_btn", "buscar", "search")
_s("picker.search", "busca", "search")
_s("picker.search_prompt", "Buscar por nome", "Search by name")
_s("picker.select", "Número do canal", "Channel number")
_s("picker.invalid", "Número inválido.", "Invalid number.")
_s("picker.from_list", "Escolher da lista", "Pick from list")
_s("picker.from_list.desc", "Seus canais e grupos, com busca", "Your channels and groups, with search")
_s("picker.manual", "Digitar manualmente", "Type manually")
_s("picker.manual.desc", "@usuario, link t.me/… ou ID numérico", "@username, t.me/… link or numeric ID")
_s("picker.how", "Como deseja informar", "How do you want to specify")
_s("picker.forum_warn", "Este grupo é um FÓRUM (com tópicos).", "This group is a FORUM (with topics).")
_s("picker.topic_prompt", "ID ou link do tópico (ENTER = todos os tópicos)", "Topic ID or link (ENTER = all topics)")
_s("picker.topic_invalid", "Formato inválido. Cole o link de uma mensagem do tópico.", "Invalid format. Paste a message link from the topic.")
_s("picker.topic_all", "Todos os tópicos", "All topics")
_s("picker.source_hl", "← ORIGEM", "← SOURCE")
_s("picker.type_channel", "Canal", "Channel")
_s("picker.type_group", "Grupo", "Group")
_s("picker.type_forum", "Fórum", "Forum")
_s("picker.header_num", "#", "#")
_s("picker.header_type", "Tipo", "Type")
_s("picker.header_id", "ID", "ID")
_s("picker.header_members", "Membros", "Members")
_s("picker.header_name", "Nome", "Name")

# ── Entidades ─────────────────────────────────────────────────────────────────
_s("entity.source", "Origem", "Source")
_s("entity.dest", "Destino", "Destination")
_s("entity.name", "Nome", "Name")
_s("entity.id", "ID", "ID")
_s("entity.type", "Tipo", "Type")
_s("entity.members", "Membros", "Members")
_s("entity.username", "Usuário", "Username")
_s("entity.source_hint", "De onde as mensagens serão copiadas", "Where messages will be copied from")
_s("entity.dest_hint", "Para onde as mensagens serão enviadas", "Where messages will be sent to")
_s("entity.not_found", "Não encontrado. Verifique se você é membro e se o link/ID está correto.",
   "Not found. Check that you're a member and the link/ID is correct.")
_s("entity.same", "Origem e destino são o mesmo canal. Escolha outro destino.",
   "Source and destination are the same channel. Pick another destination.")

# ── Cópia ─────────────────────────────────────────────────────────────────────
_s("copy.title", "COPIAR CANAL", "COPY CHANNEL")
_s("copy.source_not_found", "Origem não encontrada", "Source not found")
_s("copy.dest_not_found", "Destino não encontrado", "Destination not found")
_s("copy.summary", "RESUMO DA CÓPIA", "COPY SUMMARY")
_s("copy.resume", "Retomar de onde parou (pula o que já foi enviado)?",
   "Resume where it stopped (skip what was already sent)?")
_s("copy.confirm", "Iniciar a cópia agora?", "Start copying now?")
_s("copy.cancelled", "Cancelado pelo usuário.", "Cancelled by the user.")
_s("copy.indexing", "Lendo mensagens da origem", "Reading messages from the source")
_s("copy.indexed", "mensagens lidas", "messages read")
_s("copy.found", "mensagens encontradas", "messages found")
_s("copy.none", "Nenhuma mensagem encontrada com esse filtro.", "No messages found with this filter.")
_s("copy.all_done", "Tudo já foi copiado antes — nada a fazer.", "Everything was already copied — nothing to do.")
_s("copy.already", "já estavam copiadas (checkpoint)", "were already copied (checkpoint)")
_s("copy.albums", "álbuns detectados", "albums detected")
_s("copy.sent", "enviadas", "sent")
_s("copy.skipped", "ignoradas", "skipped")
_s("copy.errors", "erros", "errors")
_s("copy.duplicates", "duplicatas evitadas", "duplicates avoided")
_s("copy.starting", "Iniciando transferência…", "Starting transfer…")
_s("copy.interrupted", "Interrompido — o progresso foi salvo e pode ser retomado.",
   "Interrupted — progress saved and can be resumed.")
_s("copy.options", "Opções desta cópia", "Options for this copy")
_s("copy.mode", "Modo de envio", "Send mode")
_s("copy.mode.copy", "Reenviar (cópia limpa, sem 'encaminhado de')", "Re-send (clean copy, no 'forwarded from')")
_s("copy.mode.forward", "Encaminhar (mais rápido, mantém 'encaminhado de')", "Forward (faster, keeps 'forwarded from')")
_s("copy.mode.hint", "Reenviar consome mais tempo e banda, mas remove a origem.",
   "Re-sending takes more time and bandwidth but removes the origin label.")
_s("copy.limit_reached", "Limite diário de {limit} mensagens atingido. Aguarde amanhã ou ajuste em Configurações.",
   "Daily limit of {limit} messages reached. Wait until tomorrow or change it in Settings.")
_s("copy.limit_warn", "Atenção: {count}/{limit} mensagens hoje.", "Warning: {count}/{limit} messages today.")

# ── Multi-destino ─────────────────────────────────────────────────────────────
_s("multi.title", "MULTI-DESTINO", "MULTI-DESTINATION")
_s("multi.dest_n", "Destino #{n} (ENTER para começar)", "Destination #{n} (ENTER to start)")
_s("multi.added", "Adicionado", "Added")
_s("multi.already", "já está na lista", "already in the list")
_s("multi.list", "Destinos selecionados", "Selected destinations")
_s("multi.no_dests", "Nenhum destino adicionado.", "No destinations added.")
_s("multi.confirm", "Copiar para {n} destino(s)?", "Copy to {n} destination(s)?")
_s("multi.dest_label", "Destino {i}/{total}", "Destination {i}/{total}")
_s("multi.failed", "Falhou", "Failed")
_s("multi.start", "Começar", "Start")
_s("multi.remove", "Remover último", "Remove last")
_s("multi.total_sent", "Total enviado em {n} destino(s)", "Total sent across {n} destination(s)")

# ── Modo espelho ──────────────────────────────────────────────────────────────
_s("mirror.title", "MODO ESPELHO", "MIRROR MODE")
_s("mirror.waiting", "MODO ESPELHO ATIVO — aguardando mensagens novas (Ctrl+C para sair)",
   "MIRROR MODE ON — waiting for new messages (Ctrl+C to exit)")
_s("mirror.stop", "Mantenha esta janela aberta. Tudo que chegar na origem será copiado.",
   "Keep this window open. Anything that arrives at the source is copied.")
_s("mirror.new_msg", "mensagem copiada (ID {id})", "message copied (ID {id})")
_s("mirror.stopped", "Modo espelho encerrado.", "Mirror mode stopped.")
_s("mirror.sent", "enviadas nesta sessão", "sent in this session")

# ── Resultados ────────────────────────────────────────────────────────────────
_s("result.title", "RESULTADO", "RESULT")
_s("result.sent", "Enviadas", "Sent")
_s("result.skipped", "Ignoradas", "Skipped")
_s("result.errors", "Erros", "Errors")
_s("result.duplicates", "Duplicatas evitadas", "Duplicates avoided")
_s("result.albums", "Álbuns", "Albums")
_s("result.time", "Tempo total", "Total time")
_s("result.speed", "Velocidade", "Speed")
_s("result.bytes", "Transferido", "Transferred")
_s("result.per_min", "msg/min", "msg/min")

# ── Ferramentas ───────────────────────────────────────────────────────────────
_s("tools.title", "FERRAMENTAS", "TOOLS")
_s("tools.resolve", "Consultar canal", "Look up channel")
_s("tools.resolve.desc", "Ver nome, ID e tipo de um canal/link", "Show name, ID and type of a channel/link")
_s("tools.resolve.title", "CONSULTAR CANAL", "LOOK UP CHANNEL")
_s("tools.resolve.hint", "Digite @usuario, link t.me/… ou ID. ENTER em branco para sair.",
   "Type @username, t.me/… link or ID. Blank ENTER to exit.")
_s("tools.resolve.query", "Canal", "Channel")
_s("tools.resolve.result", "DADOS DO CANAL", "CHANNEL DATA")
_s("tools.resolve.not_found", "Não encontrado", "Not found")
_s("tools.resolve.name", "Nome", "Name")
_s("tools.resolve.type", "Tipo", "Type")
_s("tools.resolve.access", "Acesso", "Access")
_s("tools.resolve.private", "Privado", "Private")
_s("tools.resolve.public", "Público", "Public")
_s("tools.stats", "Estatísticas", "Statistics")
_s("tools.stats.desc", "Números de uso e limite diário", "Usage numbers and daily limit")
_s("tools.stats.title", "ESTATÍSTICAS", "STATISTICS")
_s("tools.stats.session", "ESTA INSTALAÇÃO", "THIS INSTALLATION")
_s("tools.stats.alltime", "HISTÓRICO DE CÓPIAS", "COPY HISTORY")
_s("tools.stats.daily", "HOJE", "TODAY")
_s("tools.stats.total", "Total", "Total")
_s("tools.stats.photos", "Fotos", "Photos")
_s("tools.stats.videos", "Vídeos", "Videos")
_s("tools.stats.audio", "Áudios", "Audio")
_s("tools.stats.documents", "Documentos", "Documents")
_s("tools.stats.text", "Textos", "Text")
_s("tools.stats.bytes", "Enviado", "Sent")
_s("tools.stats.sessions", "Sessões", "Sessions")
_s("tools.stats.duplicates", "Duplicatas", "Duplicates")
_s("tools.stats.errors", "Erros", "Errors")
_s("tools.stats.moved", "Dados", "Data")
_s("tools.stats.today", "Enviadas hoje", "Sent today")
_s("tools.stats.limit", "Limite diário", "Daily limit")
_s("tools.stats.remaining", "Restantes", "Remaining")
_s("tools.stats.recent", "ÚLTIMAS CÓPIAS", "LATEST COPIES")
_s("tools.stats.last_run", "Última execução", "Last run")
_s("tools.retry", "Fila de repetição", "Retry queue")
_s("tools.retry.desc", "Mensagens que falharam e podem ser reenviadas", "Messages that failed and can be re-sent")
_s("tools.retry.title", "FILA DE REPETIÇÃO", "RETRY QUEUE")
_s("tools.retry.queue", "PENDENTES", "PENDING")
_s("tools.retry.pending", "Aguardando", "Waiting")
_s("tools.retry.total", "Total", "Total")
_s("tools.retry.attempts", "Tentativas somadas", "Total attempts")
_s("tools.retry.empty", "Fila vazia — nenhuma mensagem pendente.", "Queue empty — no pending messages.")
_s("tools.retry.retry_now", "Tentar reenviar agora?", "Try re-sending now?")
_s("tools.retry.clear", "Limpar a fila?", "Clear the queue?")
_s("tools.retry.cleared", "itens removidos", "items removed")
_s("tools.retry.done", "reprocessadas", "reprocessed")
_s("tools.dedup", "Deduplicação", "Deduplication")
_s("tools.dedup.desc", "Ver estado das camadas de proteção", "Inspect the protection layers")
_s("tools.dedup.title", "DEDUPLICAÇÃO", "DEDUPLICATION")
_s("tools.dedup.lru", "CACHE LRU", "LRU CACHE")
_s("tools.dedup.bloom", "BLOOM FILTER", "BLOOM FILTER")
_s("tools.dedup.db", "BANCO DE DADOS", "DATABASE")
_s("tools.dedup.items", "Itens", "Items")
_s("tools.dedup.usage", "Uso", "Usage")
_s("tools.dedup.hashes", "Funções hash", "Hash functions")
_s("tools.dedup.memory", "Memória", "Memory")
_s("tools.dedup.fp", "Falsos positivos", "False positives")
_s("tools.dedup.history", "Hashes guardados", "Stored hashes")
_s("tools.dedup.size", "Tamanho do banco", "Database size")
_s("tools.dedup.reset", "Limpar cache em memória?", "Clear in-memory cache?")
_s("tools.dedup.reset_done", "Cache limpo (o banco permanece intacto).", "Cache cleared (database untouched).")
_s("tools.maint", "Manutenção", "Maintenance")
_s("tools.maint.desc", "Limpar temporários, otimizar banco e backup", "Clean temp files, optimize database and back up")
_s("tools.maint.title", "MANUTENÇÃO", "MAINTENANCE")
_s("tools.maint.tmp_found", "arquivos temporários", "temporary files")
_s("tools.maint.tmp_delete", "Apagar os arquivos temporários?", "Delete temporary files?")
_s("tools.maint.tmp_cleared", "Temporários removidos", "Temp files removed")
_s("tools.maint.tmp_none", "Nenhum arquivo temporário.", "No temporary files.")
_s("tools.maint.db_prune", "Remover histórico com mais de 30 dias?", "Remove history older than 30 days?")
_s("tools.maint.db_done", "registros antigos removidos", "old records removed")
_s("tools.maint.db_vacuum", "Compactar o banco de dados (VACUUM)?", "Compact the database (VACUUM)?")
_s("tools.maint.db_vacuum_done", "Banco compactado.", "Database compacted.")
_s("tools.maint.backup", "Criar backup do banco agora?", "Create a database backup now?")
_s("tools.maint.backup_done", "Backup criado em", "Backup created at")
_s("tools.maint.health", "SAÚDE DO SISTEMA", "SYSTEM HEALTH")
_s("tools.maint.integrity", "Integridade do banco", "Database integrity")
_s("tools.maint.folders", "Pastas de dados", "Data folders")

# ── Configurações ─────────────────────────────────────────────────────────────
_s("settings.title", "CONFIGURAÇÕES", "SETTINGS")
_s("settings.current", "CONFIGURAÇÃO ATUAL", "CURRENT SETTINGS")
_s("settings.delay", "Intervalo entre envios", "Delay between sends")
_s("settings.workers", "Workers de download", "Download workers")
_s("settings.dedup", "Deduplicação", "Deduplication")
_s("settings.resume", "Retomar automaticamente", "Auto-resume")
_s("settings.forward", "Modo de envio", "Send mode")
_s("settings.signature", "Assinatura", "Signature")
_s("settings.on", "ativado ✓", "on ✓")
_s("settings.off", "desativado ✗", "off ✗")
_s("settings.delay.option", "Intervalo entre envios", "Send delay")
_s("settings.delay.hint", "Mínimo 0,1 s. Recomendado 0,9 s (evita bloqueio do Telegram).",
   "Minimum 0.1 s. Recommended 0.9 s (avoids Telegram flood blocks).")
_s("settings.delay.title", "Novo intervalo em segundos (atual: {current}s)", "New delay in seconds (current: {current}s)")
_s("settings.delay.set", "Intervalo definido para {val}s", "Delay set to {val}s")
_s("settings.delay.invalid", "Número inválido.", "Invalid number.")
_s("settings.dedup.toggle", "Alternar deduplicação", "Toggle deduplication")
_s("settings.workers.option", "Workers (paralelismo)", "Workers (parallelism)")
_s("settings.workers.hint", "Mais workers = mais rápido. Recomendado para o seu PC: {rec}.",
   "More workers = faster. Recommended for your PC: {rec}.")
_s("settings.workers.set", "Workers definidos: {val}", "Workers set to {val}")
_s("settings.workers.title", "Número de workers (1-{max}, atual: {current})", "Workers (1-{max}, current: {current})")
_s("settings.forward.toggle", "Alternar modo de envio", "Toggle send mode")
_s("settings.forward.set", "Modo de envio: {val}", "Send mode: {val}")
_s("settings.resume.toggle", "Alternar retomada automática", "Toggle auto-resume")
_s("settings.signature.option", "Assinatura no fim das mensagens", "Signature at the end of messages")
_s("settings.signature.hint", "Deixe em branco para remover. Ex.: @meucanal",
   "Leave blank to remove. E.g. @mychannel")
_s("settings.signature.set", "Assinatura definida.", "Signature set.")
_s("settings.logs.export", "Exportar logs", "Export logs")
_s("settings.logs.desc", "Salvar o log em data/exports/", "Save the log to data/exports/")
_s("settings.logs.export_confirm", "Exportar o log agora?", "Export the log now?")
_s("settings.logs.none", "Nenhum log encontrado.", "No log file found.")
_s("settings.logs.done", "Exportado para", "Exported to")
_s("settings.creds.reset", "Redefinir credenciais", "Reset credentials")
_s("settings.creds.desc", "Apagar credenciais e sessão do Telegram", "Delete credentials and Telegram session")
_s("settings.creds.confirm", "Isso apaga as credenciais salvas e a sessão atual. Você precisará configurar tudo de novo.",
   "This deletes saved credentials and the current session. You'll have to set everything up again.")
_s("settings.creds.confirm_short", "Confirmar reset de credenciais?", "Confirm credential reset?")
_s("settings.creds.cleared", "Credenciais removidas. O programa será encerrado.", "Credentials cleared. The app will exit.")
_s("settings.lang", "Idioma / Language", "Language / Idioma")
_s("settings.lang.desc", "Mudar o idioma da interface", "Change the interface language")
_s("settings.lang.title", "IDIOMA", "LANGUAGE")
_s("settings.lang.current", "atual", "current")
_s("settings.lang.set", "Idioma atualizado.", "Language updated.")
_s("settings.about", "Sobre o TELECOPY", "About TELECOPY")
_s("settings.status", "ESTADO ATUAL", "CURRENT STATE")
_s("settings.data_reset", "Apagar histórico de cópias", "Clear copy history")
_s("settings.data_reset.desc", "Remove checkpoints e deduplicação (não apaga credenciais)",
   "Removes checkpoints and dedup data (keeps credentials)")
_s("settings.data_reset.confirm", "Isso fará o programa copiar tudo novamente. Confirmar?",
   "This will make the app copy everything again. Confirm?")
_s("settings.data_reset.done", "Histórico de cópias apagado.", "Copy history cleared.")

# ── Sobre ─────────────────────────────────────────────────────────────────────
_s("about.title", "SOBRE", "ABOUT")
_s("about.version", "Versão", "Version")
_s("about.author", "Autor", "Author")
_s("about.license", "Licença", "License")
_s("about.arch", "Arquitetura", "Architecture")
_s("about.arch.val", "Async · SQLite WAL · AES-256-GCM", "Async · SQLite WAL · AES-256-GCM")
_s("about.dedup", "Deduplicação", "Deduplication")
_s("about.dedup.val", "LRU + Bloom Filter + SQLite", "LRU + Bloom Filter + SQLite")
_s("about.system", "Sistema", "System")
_s("about.python", "Python", "Python")
_s("about.data_dir", "Pasta de dados", "Data folder")
_s("about.site", "Repositório", "Repository")

# ── Progresso ─────────────────────────────────────────────────────────────────
_s("progress.copying", "Copiando", "Copying")
_s("progress.downloading", "Baixando", "Downloading")
_s("progress.uploading", "Enviando", "Uploading")
_s("progress.flood", "Telegram pediu pausa de {sec}s (anti-flood)", "Telegram asked to wait {sec}s (anti-flood)")
_s("progress.download_fail", "Falha no download", "Download failed")
_s("progress.upload_fail", "Falha no envio", "Upload failed")
_s("progress.retry", "Tentativa {n}/{max}", "Attempt {n}/{max}")
_s("progress.eta", "restam", "left")
_s("progress.speed", "velocidade", "speed")
_s("progress.done", "Concluído", "Done")
_s("progress.waiting", "Aguardando", "Waiting")

# ── Retomada / fila ───────────────────────────────────────────────────────────
_s("queue.retry_start", "Reprocessando mensagens que falharam…", "Re-processing failed messages…")
_s("queue.nothing", "Nada para reprocessar.", "Nothing to re-process.")

# ── Limites ───────────────────────────────────────────────────────────────────
_s("limit.reached", "Limite diário atingido: {count}/{limit} mensagens hoje.", "Daily limit reached: {count}/{limit} messages today.")
_s("limit.warn", "Atenção: {count}/{limit} mensagens hoje ({remaining} restantes).", "Warning: {count}/{limit} messages today ({remaining} left).")

# ── Prompts genéricos ─────────────────────────────────────────────────────────
_s("prompt.continue", "Pressione ENTER para continuar", "Press ENTER to continue")
_s("prompt.required", "Este campo é obrigatório.", "This field is required.")
_s("prompt.yes_no", "[s/N]", "[y/N]")
_s("prompt.yes_chars", "s sim y yes", "y yes s sim")
_s("prompt.no_chars", "n nao não no", "n no nao")

# ── Encerramento ──────────────────────────────────────────────────────────────
_s("shutdown.saving", "Salvando estatísticas…", "Saving statistics…")
_s("shutdown.db", "Fechando banco de dados…", "Closing database…")
_s("shutdown.disc", "Desconectando do Telegram…", "Disconnecting from Telegram…")
_s("shutdown.bye", "Até logo! / Goodbye!", "Goodbye! / Até logo!")

# ── Inicialização ─────────────────────────────────────────────────────────────
_s("init.loading", "Inicializando", "Initializing")
_s("init.checking", "Verificando integridade dos dados", "Checking data integrity")
_s("init.locked", "O TELECOPY já está aberto em outra janela.", "TELECOPY is already running in another window.")
_s("init.recovered", "Cofre de credenciais ilegível neste computador — será necessário entrar novamente.",
   "Credentials vault unreadable on this computer — you'll need to sign in again.")
_s("init.python", "Python {ver}", "Python {ver}")

# ── Filtros ───────────────────────────────────────────────────────────────────
_s("filter.title", "FILTRO DE MENSAGENS", "MESSAGE FILTER")
_s("filter.default", "Filtro", "Filter")
_s("filter.selected", "Filtro aplicado", "Filter applied")
_s("filter.back_hint", "ENTER = todas as mensagens", "ENTER = all messages")
_s("filter.all", "Todas as mensagens", "All messages")
_s("filter.all.desc", "Sem filtro — copia tudo", "No filter — copies everything")
_s("filter.photos", "Somente fotos", "Photos only")
_s("filter.photos.desc", "Imagens enviadas como foto", "Images sent as photos")
_s("filter.videos", "Somente vídeos", "Videos only")
_s("filter.videos.desc", "Arquivos de vídeo", "Video files")
_s("filter.audio", "Somente áudios", "Audio only")
_s("filter.audio.desc", "Músicas, vozes e áudios", "Music, voice notes and audio")
_s("filter.documents", "Somente documentos", "Documents only")
_s("filter.documents.desc", "PDFs, ZIPs e outros arquivos", "PDFs, ZIPs and other files")
_s("filter.text", "Somente textos", "Text only")
_s("filter.text.desc", "Mensagens sem mídia", "Messages without media")
_s("filter.links", "Somente com links", "With links only")
_s("filter.links.desc", "Mensagens que contêm URLs", "Messages containing URLs")
_s("filter.media", "Somente mídia", "Media only")
_s("filter.media.desc", "Fotos, vídeos, áudios e documentos", "Photos, videos, audio and documents")
_s("filter.large", "Arquivos grandes", "Large files")
_s("filter.large.desc", "Arquivos acima de 50 MB", "Files over 50 MB")
_s("filter.recent", "Últimos 7 dias", "Last 7 days")
_s("filter.recent.desc", "Somente mensagens recentes", "Only recent messages")
_s("filter.original", "Somente originais", "Originals only")
_s("filter.original.desc", "Ignora mensagens encaminhadas", "Ignores forwarded messages")

# ── Erros comuns ──────────────────────────────────────────────────────────────
_s("err.channel_private", "Este canal é privado e você não é membro.", "This channel is private and you're not a member.")
_s("err.no_permission", "Sem permissão para enviar neste destino. Você precisa ser administrador.",
   "No permission to send to this destination. You must be an admin.")
_s("err.generic", "Erro inesperado", "Unexpected error")
_s("err.need_admin", "Você precisa ser ADMINISTRADOR no canal de destino.",
   "You must be an ADMIN in the destination channel.")
_s("err.db_busy", "O banco de dados está ocupado. Feche outras instâncias do TELECOPY.",
   "The database is busy. Close other TELECOPY instances.")

_LANG: str = "pt"
_SUPPORTED: Tuple[str, ...] = ("pt", "en")


def set_lang(lang: str) -> None:
    global _LANG
    if lang in _SUPPORTED:
        _LANG = lang


def get_lang() -> str:
    return _LANG


def t(key: str, **kwargs) -> str:
    entry = _STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(_LANG) or entry.get("pt") or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError, IndexError):
            pass
    return text


def available_languages() -> Tuple[str, ...]:
    return _SUPPORTED


def all_keys() -> Tuple[str, ...]:
    return tuple(_STRINGS.keys())


def missing(lang: str) -> Tuple[str, ...]:
    return tuple(k for k, v in _STRINGS.items() if not v.get(lang))
