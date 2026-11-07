"""
i18n.py — Internationalisation (PT-BR / EN).

v3.1: strings de onboarding, guia de credenciais, manutenção completa,
      about aprimorado, configurações expandidas.
"""

from __future__ import annotations
from typing import Dict

_STRINGS: Dict[str, Dict[str, str]] = {

    # ── Geral ─────────────────────────────────────────────────────────────────
    "app.tagline": {"pt": "Replicador de Canais Telegram", "en": "Telegram Channel Replicator"},
    "app.tech":    {"pt": "SQLite WAL  ·  AES-256-GCM  ·  Bloom Filter  ·  Async", "en": "SQLite WAL  ·  AES-256-GCM  ·  Bloom Filter  ·  Async"},
    "app.by":      {"pt": "por", "en": "by"},

    # ── Onboarding (wizard para novos usuários) ───────────────────────────────
    "onboard.title":    {"pt": "BEM-VINDO AO TELECOPY", "en": "WELCOME TO TELECOPY"},
    "onboard.subtitle": {"pt": "Siga os passos abaixo para começar / Follow the steps below to start", "en": "Follow the steps below to start"},

    "onboard.step1.title": {
        "pt": "Obtenha suas credenciais Telegram",
        "en": "Get your Telegram credentials",
    },
    "onboard.step1.body": {
        "pt": "Acesse https://my.telegram.org/apps e faça login com sua conta Telegram. Crie um novo App e copie o API ID (número) e o API Hash (string de 32 caracteres).",
        "en": "Go to https://my.telegram.org/apps and log in with your Telegram account. Create a new App and copy the API ID (a number) and the API Hash (32-character string).",
    },
    "onboard.step2.title": {
        "pt": "Configure o TELECOPY",
        "en": "Configure TELECOPY",
    },
    "onboard.step2.body": {
        "pt": "Na próxima tela, informe o API ID e o API Hash obtidos no passo anterior. Suas credenciais são criptografadas localmente com AES-256-GCM.",
        "en": "On the next screen, enter the API ID and API Hash you got in step 1. Your credentials are stored locally with AES-256-GCM encryption.",
    },
    "onboard.step3.title": {
        "pt": "Verifique sua conta",
        "en": "Verify your account",
    },
    "onboard.step3.body": {
        "pt": "Informe seu número de telefone com código do país (ex.: +55 11 99999-9999). O Telegram enviará um código de verificação no app. Se tiver senha de dois fatores, ela será solicitada em seguida.",
        "en": "Enter your phone number with country code (e.g. +1 555 000-0000). Telegram will send a verification code in the app. If you have 2-factor auth enabled, your password will be requested next.",
    },
    "onboard.step4.title": {
        "pt": "Pronto! Use o menu para copiar canais",
        "en": "Done! Use the menu to copy channels",
    },
    "onboard.step4.body": {
        "pt": "Use a opção [1] Copiar canal para replicar histórico completo, [2] Multi-destino para enviar para vários canais de uma vez, ou [3] Modo Espelho para sincronização em tempo real.",
        "en": "Use option [1] Copy channel to replicate full history, [2] Multi-destination to send to several channels at once, or [3] Mirror Mode for real-time sync.",
    },

    "onboard.tip": {
        "pt": "Dica: As credenciais são salvas após o primeiro login. Você não precisará informá-las novamente.",
        "en": "Tip: Credentials are saved after the first login. You won't need to enter them again.",
    },
    "onboard.continue": {"pt": "Pressione ENTER para continuar com a configuração", "en": "Press ENTER to proceed with setup"},

    # ── Auth ──────────────────────────────────────────────────────────────────
    "auth.first_time.title":    {"pt": "CONFIGURAÇÃO INICIAL",   "en": "INITIAL SETUP"},
    "auth.first_time.subtitle": {
        "pt": "Credenciais criptografadas com AES-256-GCM e armazenadas localmente.",
        "en": "Credentials encrypted with AES-256-GCM and stored locally.",
    },

    "auth.creds_guide.title": {
        "pt": "Como obter suas credenciais:",
        "en": "How to get your credentials:",
    },
    "auth.creds_guide.steps": {
        "pt": "Acesse: https://my.telegram.org/apps | Faça login com seu número Telegram | Clique em 'API development tools' | Crie um novo App (preencha qualquer nome) | Copie o 'App api_id' e o 'App api_hash'",
        "en": "Go to: https://my.telegram.org/apps | Log in with your Telegram number | Click 'API development tools' | Create a new App (any name) | Copy the 'App api_id' and 'App api_hash'",
    },

    "auth.api_id":          {"pt": "API ID (número)",                        "en": "API ID (number)"},
    "auth.api_id_invalid":  {"pt": "O API ID deve ser um número inteiro.",    "en": "API ID must be an integer."},
    "auth.api_hash":        {"pt": "API Hash (32 caracteres)",                "en": "API Hash (32 characters)"},
    "auth.api_hash_short":  {"pt": "API Hash parece inválido (muito curto).", "en": "API Hash looks invalid (too short)."},
    "auth.creds_saved":     {"pt": "Credenciais salvas com criptografia.",    "en": "Credentials saved with encryption."},
    "auth.conn_failed":     {"pt": "Falha ao conectar",                       "en": "Connection failed"},
    "auth.signed_in":       {"pt": "Conectado como",                          "en": "Signed in as"},

    "auth.sign_in.title":    {"pt": "VERIFICAÇÃO DE CONTA",    "en": "ACCOUNT VERIFICATION"},
    "auth.sign_in.subtitle": {
        "pt": "O Telegram enviará um código no app. Caso já esteja logado em outro dispositivo, o código aparecerá lá.",
        "en": "Telegram will send a code in the app. If you're logged in on another device, the code will appear there.",
    },

    "auth.phone":           {"pt": "Número de telefone (ex.: +55 11 99999-9999)",     "en": "Phone number (e.g. +1 555 000-0000)"},
    "auth.code_send_fail":  {"pt": "Não foi possível enviar o código",                "en": "Could not send verification code"},
    "auth.code_hint":       {"pt": "Abra o Telegram e confira o código recebido.",     "en": "Open Telegram and check the verification code."},
    "auth.code":            {"pt": "Código de verificação (tentativa {n}/3)",          "en": "Verification code (attempt {n}/3)"},
    "auth.code_wrong":      {"pt": "Código incorreto. Tente novamente.",               "en": "Wrong code. Please try again."},
    "auth.code_expired":    {"pt": "Código expirado. Enviando novo…",                 "en": "Code expired. Sending a new one…"},
    "auth.2fa_hint":        {"pt": "Sua conta tem verificação em dois fatores ativada.", "en": "Your account has two-factor authentication enabled."},
    "auth.2fa":             {"pt": "Senha de dois fatores",                            "en": "Two-factor password"},
    "auth.2fa_fail":        {"pt": "Falha na autenticação em dois fatores",            "en": "Two-factor authentication failed"},
    "auth.too_many":        {"pt": "Muitas tentativas incorretas. Encerrando.",        "en": "Too many failed attempts. Exiting."},
    "auth.fail":            {"pt": "Autenticação falhou. Encerrando.",                 "en": "Authentication failed. Exiting."},
    "auth.get_creds":       {"pt": "Obtenha suas credenciais em: https://my.telegram.org/apps", "en": "Get your credentials at: https://my.telegram.org/apps"},

    # ── Menu principal ────────────────────────────────────────────────────────
    "menu.title":        {"pt": "TELECOPY",           "en": "TELECOPY"},
    "menu.copy":         {"pt": "Copiar canal",        "en": "Copy channel"},
    "menu.copy.desc":    {"pt": "Replica histórico completo de um canal", "en": "Replicate full history of a channel"},
    "menu.multi":        {"pt": "Multi-destino",       "en": "Multi-destination"},
    "menu.multi.desc":   {"pt": "Enviar para vários canais de uma vez",   "en": "Send to several channels at once"},
    "menu.mirror":       {"pt": "Modo Espelho",         "en": "Mirror Mode"},
    "menu.mirror.desc":  {"pt": "Sincroniza mensagens em tempo real 24/7","en": "Sync new messages in real-time 24/7"},
    "menu.tools":        {"pt": "Ferramentas",          "en": "Tools"},
    "menu.tools.desc":   {"pt": "Utilitários e diagnósticos",            "en": "Utilities and diagnostics"},
    "menu.settings":     {"pt": "Configurações",        "en": "Settings"},
    "menu.settings.desc":{"pt": "Personalizar o aplicativo",             "en": "Configure the application"},
    "menu.exit":         {"pt": "Sair",                 "en": "Exit"},
    "menu.back":         {"pt": "Voltar",               "en": "Back"},
    "menu.option":       {"pt": "Opção",                "en": "Option"},

    # ── Mirror Mode ───────────────────────────────────────────────────────────
    "mirror.title":    {"pt": "MODO ESPELHO",                             "en": "MIRROR MODE"},
    "mirror.waiting":  {"pt": "Modo Espelho ATIVADO — aguardando novas mensagens…", "en": "Mirror Mode ON — waiting for new messages…"},
    "mirror.stop":     {"pt": "(Pressione Ctrl+C para parar)",            "en": "(Press Ctrl+C to stop)"},
    "mirror.new_msg":  {"pt": "Nova mensagem clonada  (ID: {id})",        "en": "New message cloned  (ID: {id})"},

    # ── Copiar canal ──────────────────────────────────────────────────────────
    "copy.title":            {"pt": "COPIAR CANAL",              "en": "COPY CHANNEL"},
    "copy.source":           {"pt": "Origem  (username, link ou ID)",     "en": "Source  (username, link or ID)"},
    "copy.source_not_found": {"pt": "Origem não encontrada",              "en": "Source not found"},
    "copy.dest":             {"pt": "Destino  (username, link ou ID)",    "en": "Destination  (username, link or ID)"},
    "copy.dest_not_found":   {"pt": "Destino não encontrado",             "en": "Destination not found"},
    "copy.resume":           {"pt": "Continuar de onde parou?",           "en": "Resume from last checkpoint?"},
    "copy.confirm":          {"pt": "Iniciar cópia agora?",               "en": "Start copying now?"},
    "copy.cancelled":        {"pt": "Cancelado pelo usuário.",             "en": "Cancelled by user."},
    "copy.indexing":         {"pt": "Indexando mensagens…",               "en": "Indexing messages…"},
    "copy.found":            {"pt": "mensagens encontradas",              "en": "messages found"},
    "copy.none":             {"pt": "Nenhuma mensagem encontrada.",        "en": "No messages found."},
    "copy.done":             {"pt": "Concluído",                          "en": "Done"},
    "copy.sent":             {"pt": "enviadas",                           "en": "sent"},
    "copy.skipped":          {"pt": "ignoradas",                          "en": "skipped"},
    "copy.errors":           {"pt": "erros",                              "en": "errors"},
    "copy.source_hint":      {"pt": "Como deseja informar a ORIGEM?",     "en": "How do you want to specify the SOURCE?"},
    "copy.dest_hint":        {"pt": "Como deseja informar o DESTINO?",    "en": "How do you want to specify the DESTINATION?"},

    # ── Multi-destino ─────────────────────────────────────────────────────────
    "multi.title":       {"pt": "MULTI-DESTINO",                                  "en": "MULTI-DESTINATION"},
    "multi.dest_n":      {"pt": "Destino #{n}  (deixe em branco para iniciar)",   "en": "Destination #{n}  (leave blank to start)"},
    "multi.added":       {"pt": "Adicionado",                                     "en": "Added"},
    "multi.no_dests":    {"pt": "Nenhum destino adicionado.",                     "en": "No destinations added."},
    "multi.confirm":     {"pt": "Copiar para {n} destino(s)?",                    "en": "Copy to {n} destination(s)?"},
    "multi.dest_label":  {"pt": "Destino {i}/{total}",                            "en": "Destination {i}/{total}"},
    "multi.failed":      {"pt": "Falhou",                                         "en": "Failed"},
    "multi.start":       {"pt": "Iniciar",                                        "en": "Start"},

    # ── Filtros ───────────────────────────────────────────────────────────────
    "filter.title":           {"pt": "FILTRO DE MENSAGENS",       "en": "MESSAGE FILTER"},
    "filter.default":         {"pt": "Filtro [padrão: todas]",    "en": "Filter [default: all]"},
    "filter.selected":        {"pt": "Filtro selecionado",        "en": "Filter selected"},
    "filter.all":             {"pt": "Todas as mensagens",        "en": "All messages"},
    "filter.all.desc":        {"pt": "Sem filtro aplicado",       "en": "No filter applied"},
    "filter.photos":          {"pt": "Fotos",                     "en": "Photos"},
    "filter.photos.desc":     {"pt": "Apenas imagens",            "en": "Images only"},
    "filter.videos":          {"pt": "Vídeos",                    "en": "Videos"},
    "filter.videos.desc":     {"pt": "Apenas arquivos de vídeo",  "en": "Video files only"},
    "filter.audio":           {"pt": "Áudio",                     "en": "Audio"},
    "filter.audio.desc":      {"pt": "Músicas e mensagens de voz","en": "Music and voice messages"},
    "filter.documents":       {"pt": "Documentos",                "en": "Documents"},
    "filter.documents.desc":  {"pt": "Arquivos (sem mídia)",      "en": "Files (non-media)"},
    "filter.text":            {"pt": "Somente texto",             "en": "Text only"},
    "filter.text.desc":       {"pt": "Mensagens sem mídia",       "en": "Messages without media"},
    "filter.links":           {"pt": "Links",                     "en": "Links"},
    "filter.links.desc":      {"pt": "Mensagens com URLs",        "en": "Messages containing URLs"},
    "filter.media":           {"pt": "Toda a mídia",              "en": "All media"},
    "filter.media.desc":      {"pt": "Fotos e vídeos",            "en": "Photos and videos"},
    "filter.large":           {"pt": "Arquivos grandes",          "en": "Large files"},
    "filter.large.desc":      {"pt": "Arquivos acima de 50 MB",   "en": "Files above 50 MB"},
    "filter.recent":          {"pt": "Recentes (7 dias)",         "en": "Recent (7 days)"},
    "filter.recent.desc":     {"pt": "Mensagens da última semana","en": "Messages from the last week"},
    "filter.original":        {"pt": "Originais",                 "en": "Originals"},
    "filter.original.desc":   {"pt": "Não encaminhadas",          "en": "Not forwarded"},
    "filter.back_hint":       {"pt": "Digite 0 para todas as mensagens, ou escolha um filtro", "en": "Enter 0 for all messages, or choose a filter"},

    # ── Ferramentas ───────────────────────────────────────────────────────────
    "tools.title":              {"pt": "FERRAMENTAS",                           "en": "TOOLS"},
    "tools.resolve":            {"pt": "Resolver entidade",                     "en": "Resolve entity"},
    "tools.resolve.desc":       {"pt": "Buscar informações de um canal/usuário","en": "Look up info of a channel/user"},
    "tools.resolve.title":      {"pt": "RESOLVER ENTIDADE",                     "en": "RESOLVE ENTITY"},
    "tools.resolve.hint":       {"pt": "Informe @usuario, link (t.me/…) ou ID numérico. Deixe em branco para voltar.", "en": "Enter @username, t.me/… link or numeric ID. Leave blank to go back."},
    "tools.resolve.query":      {"pt": "Username, link ou ID",                  "en": "Username, link or ID"},
    "tools.resolve.result":     {"pt": "Resultado",                             "en": "Result"},
    "tools.resolve.not_found":  {"pt": "Não encontrado",                        "en": "Not found"},
    "tools.resolve.name":       {"pt": "Nome",                                  "en": "Name"},
    "tools.resolve.type":       {"pt": "Tipo",                                  "en": "Type"},
    "tools.resolve.access":     {"pt": "Acesso",                                "en": "Access"},
    "tools.resolve.private":    {"pt": "Privado (sem @username)",               "en": "Private (no @username)"},
    "tools.resolve.public":     {"pt": "Público",                               "en": "Public"},

    "tools.stats":              {"pt": "Estatísticas",                          "en": "Statistics"},
    "tools.stats.desc":         {"pt": "Métricas da sessão e histórico total",  "en": "Session and lifetime metrics"},
    "tools.stats.title":        {"pt": "ESTATÍSTICAS",                          "en": "STATISTICS"},
    "tools.stats.session":      {"pt": "ESTA SESSÃO",                           "en": "THIS SESSION"},
    "tools.stats.alltime":      {"pt": "HISTÓRICO TOTAL (BANCO DE DADOS)",      "en": "ALL TIME (DATABASE)"},
    "tools.stats.daily":        {"pt": "LIMITE DIÁRIO",                         "en": "DAILY LIMIT"},
    "tools.stats.total":        {"pt": "Total enviado",                         "en": "Total sent"},
    "tools.stats.photos":       {"pt": "Fotos",                                 "en": "Photos"},
    "tools.stats.videos":       {"pt": "Vídeos",                                "en": "Videos"},
    "tools.stats.audio":        {"pt": "Áudio",                                 "en": "Audio"},
    "tools.stats.documents":    {"pt": "Documentos",                            "en": "Documents"},
    "tools.stats.bytes":        {"pt": "Dados enviados",                        "en": "Data sent"},
    "tools.stats.sessions":     {"pt": "Sessões",                               "en": "Sessions"},
    "tools.stats.duplicates":   {"pt": "Duplicatas evitadas",                   "en": "Duplicates avoided"},
    "tools.stats.errors":       {"pt": "Erros",                                 "en": "Errors"},
    "tools.stats.moved":        {"pt": "Dados movidos",                         "en": "Data moved"},
    "tools.stats.today":        {"pt": "Processadas hoje",                      "en": "Processed today"},
    "tools.stats.limit":        {"pt": "Limite diário",                         "en": "Daily limit"},
    "tools.stats.remaining":    {"pt": "Restante",                              "en": "Remaining"},

    "tools.retry":              {"pt": "Fila de retry",                         "en": "Retry queue"},
    "tools.retry.desc":         {"pt": "Mensagens com falha aguardando reenvio","en": "Failed messages waiting to retry"},
    "tools.retry.title":        {"pt": "FILA DE RETRY",                         "en": "RETRY QUEUE"},
    "tools.retry.queue":        {"pt": "STATUS DA FILA",                        "en": "QUEUE STATUS"},
    "tools.retry.pending":      {"pt": "Pendentes",                             "en": "Pending"},
    "tools.retry.exhausted":    {"pt": "Esgotados (sem retentativas)",          "en": "Exhausted (no more retries)"},
    "tools.retry.total":        {"pt": "Total",                                 "en": "Total"},
    "tools.retry.clear":        {"pt": "Limpar itens esgotados?",               "en": "Clear exhausted jobs?"},
    "tools.retry.cleared":      {"pt": "itens removidos.",                      "en": "jobs removed."},

    "tools.dedup":              {"pt": "Deduplicação",                          "en": "Dedup status"},
    "tools.dedup.desc":         {"pt": "Status do Bloom Filter e cache LRU",    "en": "Bloom filter and LRU cache stats"},
    "tools.dedup.title":        {"pt": "MOTOR DE DEDUPLICAÇÃO",                 "en": "DEDUP ENGINE STATUS"},
    "tools.dedup.lru":          {"pt": "CACHE LRU (em memória)",                "en": "LRU CACHE (in-memory)"},
    "tools.dedup.bloom":        {"pt": "BLOOM FILTER (probabilístico)",         "en": "BLOOM FILTER (probabilistic)"},
    "tools.dedup.items":        {"pt": "Itens",                                 "en": "Items"},
    "tools.dedup.usage":        {"pt": "Uso do cache",                          "en": "Cache usage"},
    "tools.dedup.hashes":       {"pt": "Funções hash",                          "en": "Hash functions"},
    "tools.dedup.memory":       {"pt": "Memória",                               "en": "Memory"},
    "tools.dedup.fp":           {"pt": "Taxa falso-positivo atual",             "en": "Current false-positive rate"},

    "tools.maint":              {"pt": "Manutenção",                            "en": "Maintenance"},
    "tools.maint.desc":         {"pt": "Limpar arquivos e compactar banco",     "en": "Clean temp files and compact DB"},
    "tools.maint.title":        {"pt": "MANUTENÇÃO DO SISTEMA",                 "en": "SYSTEM MAINTENANCE"},
    "tools.maint.tmp_found":    {"pt": "arquivos temporários encontrados",      "en": "temp files found"},
    "tools.maint.tmp_delete":   {"pt": "Excluir arquivos temporários?",         "en": "Delete temp files?"},
    "tools.maint.tmp_cleared":  {"pt": "Arquivos temporários removidos.",       "en": "Temp files cleared."},
    "tools.maint.tmp_none":     {"pt": "Nenhum arquivo temporário.",            "en": "No temp files found."},
    "tools.maint.db_prune":     {"pt": "Remover registros antigos (>30 dias)?", "en": "Remove old records (>30 days)?"},
    "tools.maint.db_done":      {"pt": "registros removidos do banco.",         "en": "old records removed from DB."},
    "tools.maint.db_vacuum":    {"pt": "Compactar banco (VACUUM)?",             "en": "Compact database (VACUUM)?"},
    "tools.maint.db_vacuum_done":{"pt":"Banco compactado com sucesso.",          "en": "Database compacted successfully."},

    # ── Configurações ─────────────────────────────────────────────────────────
    "settings.title":              {"pt": "CONFIGURAÇÕES",                       "en": "SETTINGS"},
    "settings.current":            {"pt": "CONFIGURAÇÃO ATUAL",                  "en": "CURRENT SETTINGS"},
    "settings.delay":              {"pt": "Intervalo entre envios",              "en": "Delay between sends"},
    "settings.workers":            {"pt": "Workers de download",                 "en": "Download workers"},
    "settings.dedup":              {"pt": "Deduplicação",                        "en": "Deduplication"},
    "settings.integrity":          {"pt": "Verificação de integridade",          "en": "Integrity check"},
    "settings.on":                 {"pt": "ativada ✓",                           "en": "on ✓"},
    "settings.off":                {"pt": "desativada ✗",                        "en": "off ✗"},

    "settings.delay.option":       {"pt": "Alterar intervalo de envio",          "en": "Change send delay"},
    "settings.delay.option.desc":  {"pt": "Segundos entre cada upload",          "en": "Seconds between each upload"},
    "settings.delay.hint":         {"pt": "Intervalo mínimo: 0.1s. Recomendado: 0.9s (evita FloodWait).", "en": "Minimum: 0.1s. Recommended: 0.9s (avoids FloodWait)."},
    "settings.delay.title":        {"pt": "Novo intervalo em segundos (atual: {current}s)", "en": "New delay in seconds (current: {current}s)"},
    "settings.delay.set":          {"pt": "Intervalo definido para {val}s",      "en": "Delay set to {val}s"},
    "settings.delay.invalid":      {"pt": "Número inválido.",                    "en": "Invalid number."},

    "settings.dedup.toggle":       {"pt": "Alternar deduplicação",               "en": "Toggle deduplication"},

    "settings.logs.export":        {"pt": "Exportar logs",                       "en": "Export logs"},
    "settings.logs.desc":          {"pt": "Salvar arquivo de log em exports/",   "en": "Save log file to exports/"},
    "settings.logs.export_confirm":{"pt": "Exportar o arquivo de log agora?",    "en": "Export the log file now?"},
    "settings.logs.none":          {"pt": "Nenhum log encontrado.",              "en": "No log file found."},
    "settings.logs.done":          {"pt": "Exportado para",                      "en": "Exported to"},

    "settings.creds.reset":        {"pt": "Redefinir credenciais",               "en": "Reset credentials"},
    "settings.creds.desc":         {"pt": "Apagar credenciais e desconectar",    "en": "Delete credentials and sign out"},
    "settings.creds.confirm":      {"pt": "Isso apagará suas credenciais salvas e encerrará a sessão atual. Você precisará configurar novamente.", "en": "This will delete your saved credentials and end the current session. You'll need to set up again."},
    "settings.creds.confirm_short":{"pt": "Confirmar reset de credenciais?",     "en": "Confirm credential reset?"},
    "settings.creds.cleared":      {"pt": "Credenciais removidas. Reinicie o programa.",  "en": "Credentials cleared. Please restart."},

    "settings.lang":               {"pt": "Idioma / Language",                   "en": "Language / Idioma"},
    "settings.lang.desc":          {"pt": "Mudar o idioma da interface",          "en": "Change interface language"},
    "settings.lang.title":         {"pt": "SELECIONAR IDIOMA",                   "en": "SELECT LANGUAGE"},
    "settings.lang.current":       {"pt": "Idioma atual",                        "en": "Current language"},
    "settings.lang.set":           {"pt": "Idioma alterado com sucesso.",         "en": "Language changed successfully."},

    "settings.about":              {"pt": "Sobre o TELECOPY",                    "en": "About TELECOPY"},

    # ── Sobre ─────────────────────────────────────────────────────────────────
    "about.title":    {"pt": "SOBRE",                                    "en": "ABOUT"},
    "about.version":  {"pt": "Versão",                                   "en": "Version"},
    "about.author":   {"pt": "Autor",                                    "en": "Author"},
    "about.license":  {"pt": "Licença",                                  "en": "License"},
    "about.arch":     {"pt": "Arquitetura",                              "en": "Architecture"},
    "about.arch.val": {"pt": "SQLite WAL · AES-256-GCM · Bloom Filter", "en": "SQLite WAL · AES-256-GCM · Bloom Filter"},
    "about.dedup":    {"pt": "Camadas de dedup",                        "en": "Dedup layers"},
    "about.dedup.val":{"pt": "Cache LRU · Bloom Filter · SQLite",       "en": "LRU Cache · Bloom Filter · SQLite"},

    # ── Resultado ─────────────────────────────────────────────────────────────
    "result.title":      {"pt": "RESULTADO",         "en": "RESULT"},
    "result.sent":       {"pt": "Enviadas",           "en": "Sent"},
    "result.skipped":    {"pt": "Ignoradas",          "en": "Skipped"},
    "result.errors":     {"pt": "Erros",              "en": "Errors"},
    "result.dedup_id":   {"pt": "Dedup (por ID)",     "en": "Dedup (by ID)"},
    "result.dedup_hash": {"pt": "Dedup (por hash)",   "en": "Dedup (by hash)"},
    "result.time":       {"pt": "Tempo",              "en": "Time"},
    "result.bytes":      {"pt": "Dados enviados",     "en": "Data sent"},

    # ── Entidade ──────────────────────────────────────────────────────────────
    "entity.source": {"pt": "ORIGEM",  "en": "SOURCE"},
    "entity.dest":   {"pt": "DESTINO", "en": "DESTINATION"},
    "entity.name":   {"pt": "Nome",    "en": "Name"},
    "entity.id":     {"pt": "ID",      "en": "ID"},
    "entity.type":   {"pt": "Tipo",    "en": "Type"},

    # ── Progresso ────────────────────────────────────────────────────────────
    "progress.copying":      {"pt": "Copiando…",                    "en": "Copying…"},
    "progress.flood":        {"pt": "FloodWait {sec}s — aguardando","en": "FloodWait {sec}s — sleeping"},
    "progress.download_fail":{"pt": "falha no download",            "en": "download failed"},
    "progress.upload_fail":  {"pt": "falha no upload",              "en": "upload failed"},

    # ── Encerramento ──────────────────────────────────────────────────────────
    "shutdown.saving": {"pt": "Salvando dados…",       "en": "Saving data…"},
    "shutdown.db":     {"pt": "Fechando banco…",        "en": "Closing database…"},
    "shutdown.disc":   {"pt": "Desconectando…",         "en": "Disconnecting…"},
    "shutdown.bye":    {"pt": "Até mais!",              "en": "Goodbye!"},

    # ── Prompts ───────────────────────────────────────────────────────────────
    "prompt.continue":  {"pt": "Pressione ENTER para continuar", "en": "Press ENTER to continue"},
    "prompt.required":  {"pt": "Este campo é obrigatório.",      "en": "This field is required."},
    "prompt.yes_no":    {"pt": "[s/N]",                          "en": "[y/N]"},
    "prompt.yes_chars": {"pt": "s y sim yes",                    "en": "y yes"},

    # ── Limite diário ─────────────────────────────────────────────────────────
    "limit.reached": {"pt": "⚠  Limite diário atingido ({count}/{limit})",                             "en": "⚠  Daily limit reached ({count}/{limit})"},
    "limit.warn":    {"pt": "⚠  Próximo do limite diário  {count}/{limit}  ({remaining} restantes)",   "en": "⚠  Approaching daily limit  {count}/{limit}  ({remaining} remaining)"},

    # ── Inicialização ─────────────────────────────────────────────────────────
    "init.loading": {"pt": "Iniciando", "en": "Initialising"},

    # ── Channel picker ────────────────────────────────────────────────────────
    "picker.title":          {"pt": "SELECIONAR CANAL / GRUPO",              "en": "SELECT CHANNEL / GROUP"},
    "picker.loading":        {"pt": "Carregando seus canais e grupos",       "en": "Loading your channels and groups"},
    "picker.found":          {"pt": "canais/grupos encontrados",             "en": "channels/groups found"},
    "picker.page":           {"pt": "Página",                               "en": "Page"},
    "picker.prev":           {"pt": "Anterior",                             "en": "Previous"},
    "picker.next":           {"pt": "Próxima",                              "en": "Next"},
    "picker.search_btn":     {"pt": "Buscar por nome",                      "en": "Search by name"},
    "picker.search":         {"pt": "Busca",                                "en": "Search"},
    "picker.search_prompt":  {"pt": "Nome para buscar",                     "en": "Search by name"},
    "picker.select":         {"pt": "Número, [N]ext, [P]rev, [/]buscar, [0]voltar", "en": "Number, [N]ext, [P]rev, [/]search, [0]back"},
    "picker.invalid":        {"pt": "Opção inválida.",                      "en": "Invalid option."},
    "picker.from_list":      {"pt": "Escolher da lista",                    "en": "Pick from list"},
    "picker.from_list.desc": {"pt": "Seus canais e grupos com busca",       "en": "Your channels and groups with search"},
    "picker.manual":         {"pt": "Digitar manualmente",                  "en": "Type manually"},
    "picker.manual.desc":    {"pt": "Username (@canal), link (t.me/…) ou ID numérico", "en": "Username (@channel), link (t.me/…) or numeric ID"},
    "picker.forum_warn":     {"pt": "O selecionado é um FÓRUM. Informe o ID ou link do tópico.", "en": "The selected entity is a FORUM. Please provide the topic ID or link."},
    "picker.topic_prompt":   {"pt": "Link de uma mensagem do tópico ou ID numérico (0 para voltar)", "en": "Topic message link or numeric ID (0 to go back)"},
    "picker.topic_invalid":  {"pt": "Formato inválido. Cole o link de uma mensagem dentro do tópico.", "en": "Invalid format. Paste the link of a message inside the topic."},
    "picker.source_hl":      {"pt": "← ORIGEM",                            "en": "← SOURCE"},
}

_LANG: str = "pt"
_SUPPORTED = ("pt", "en")


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
        except (KeyError, ValueError):
            pass
    return text


def available_languages() -> tuple:
    return _SUPPORTED
