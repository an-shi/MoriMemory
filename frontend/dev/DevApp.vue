<script setup lang="ts">
import { MemoryApiClient, MemoryExplorer, resolveErrorMessage, type HealthResponse, type MemoryApi } from '@moriai/memory-ui'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useDisplay, useTheme } from 'vuetify'

const theme = useTheme()
const { mdAndUp } = useDisplay()
const isDark = computed(() => theme.current.value.dark)
const drawer = ref(true)

watch(
  mdAndUp,
  (desktop) => {
    drawer.value = desktop
  },
  { immediate: true },
)

const token = ref(localStorage.getItem('mori-memory.dev-token') ?? import.meta.env.VITE_MORIMEMORY_TOKEN ?? '')
const userId = ref(localStorage.getItem('mori-memory.dev-user') ?? 'local')
const connected = ref(false)
const connecting = ref(false)
const error = ref('')
const health = ref<HealthResponse | null>(null)
const settingsOpen = ref(false)
const draft = reactive({ token: token.value, userId: userId.value })
const initialView =
  new URLSearchParams(window.location.search).get('view') === 'graph' ? 'graph' : 'table'

const client = ref<MemoryApi | null>(null)

function buildClient(): MemoryApiClient {
  return new MemoryApiClient({
    baseUrl: import.meta.env.VITE_MORIMEMORY_API ?? '/api/v1',
    token: () => token.value,
    headers: () => ({
      'X-Mori-User-Id': userId.value || 'local',
      'X-Mori-Role': 'admin',
    }),
  })
}

async function connect(): Promise<void> {
  if (!token.value.trim()) {
    error.value = '请输入服务令牌。运行 `uv run morimemory token` 获取。'
    return
  }
  connecting.value = true
  error.value = ''
  try {
    const next = buildClient()
    health.value = await next.health()
    await next.stats('manage')
    localStorage.setItem('mori-memory.dev-token', token.value.trim())
    localStorage.setItem('mori-memory.dev-user', userId.value.trim() || 'local')
    client.value = next
    connected.value = true
  } catch (caught) {
    error.value = resolveErrorMessage(caught)
    connected.value = false
  } finally {
    connecting.value = false
  }
}

function disconnect(): void {
  connected.value = false
  client.value = null
  health.value = null
}

function toggleTheme(): void {
  const wasDark = isDark.value
  const next = wasDark ? 'moribot-light' : 'moribot-dark'
  theme.global.name.value = next
  localStorage.setItem('mori-memory.dev-theme', wasDark ? 'light' : 'dark')
  document.documentElement.style.colorScheme = wasDark ? 'light' : 'dark'
}

function openSettings(): void {
  draft.token = token.value
  draft.userId = userId.value
  settingsOpen.value = true
}

async function saveSettings(): Promise<void> {
  token.value = draft.token
  userId.value = draft.userId
  settingsOpen.value = false
  await connect()
}

onMounted(() => {
  document.documentElement.style.colorScheme = isDark.value ? 'dark' : 'light'
  const params = new URLSearchParams(window.location.search)
  const queryToken = params.get('token')
  if (queryToken) {
    token.value = queryToken
  }
  if (params.get('theme') === 'light') {
    theme.global.name.value = 'moribot-light'
    document.documentElement.style.colorScheme = 'light'
  } else if (params.get('theme') === 'dark') {
    theme.global.name.value = 'moribot-dark'
    document.documentElement.style.colorScheme = 'dark'
  }
  if (token.value) {
    void connect()
  }
})
</script>

<template>
  <v-app>
    <v-navigation-drawer
      v-model="drawer"
      :permanent="mdAndUp"
      :temporary="!mdAndUp"
      width="252"
      class="dev-drawer"
    >
      <div class="brand">
        <div class="brand__mark">
          <v-icon size="22">mdi-brain</v-icon>
        </div>
        <div>
          <strong>MoriMemory</strong>
          <small>Memory Engine</small>
        </div>
      </div>

      <p class="nav-label">控制台</p>
      <nav class="nav">
        <a class="nav-item nav-item--active" href="#">
          <v-icon size="18">mdi-memory</v-icon>
          <span>记忆探索器</span>
        </a>
        <span class="nav-item nav-item--disabled">
          <v-icon size="18">mdi-tune-variant</v-icon>
          <span>记忆配置</span>
          <em>API</em>
        </span>
        <span class="nav-item nav-item--disabled">
          <v-icon size="18">mdi-progress-clock</v-icon>
          <span>后台任务</span>
          <em>API</em>
        </span>
      </nav>

      <div class="drawer-spacer" />

      <div class="runtime">
        <div class="runtime__head">
          <i :class="{ live: connected }" />
          <span>Memory Service</span>
          <strong>{{ connected ? '运行中' : '未连接' }}</strong>
        </div>
        <div class="runtime__meta">
          <span>v{{ health?.version ?? '0.1.0' }}</span>
          <span>{{ health?.database.fts5 ? 'FTS5' : 'FTS off' }}</span>
          <span>{{ health?.embedding.provider ?? '—' }}</span>
        </div>
      </div>
    </v-navigation-drawer>

    <v-app-bar flat height="68" class="dev-appbar">
      <v-app-bar-nav-icon v-if="!mdAndUp" @click="drawer = true" />
      <div class="appbar-title">
        <span>MORI MEMORY · EXPLORER</span>
        <strong>记忆探索器</strong>
      </div>
      <v-spacer />
      <v-chip v-if="health" size="small" variant="tonal" color="success" class="mr-2">
        <v-icon start size="14">mdi-check-circle-outline</v-icon>
        {{ health.database.schema_version ? `schema v${health.database.schema_version}` : 'ready' }}
      </v-chip>
      <v-btn icon variant="text" class="mr-1" @click="toggleTheme">
        <v-icon>{{ isDark ? 'mdi-weather-sunny' : 'mdi-weather-night' }}</v-icon>
      </v-btn>
      <v-btn variant="tonal" size="small" prepend-icon="mdi-cog-outline" @click="openSettings">
        连接设置
      </v-btn>
    </v-app-bar>

    <v-main class="dev-main">
      <div class="dev-content">
        <div v-if="!connected" class="connect-card">
          <div class="connect-card__icon">
            <v-icon size="34" color="primary">mdi-lan-connect</v-icon>
          </div>
          <h1>连接 MoriMemory 服务</h1>
          <p>
            开发环境通过 Vite 代理访问 <code>http://127.0.0.1:6190</code>。请先启动后端，并填入服务令牌。
          </p>
          <v-text-field
            v-model="token"
            label="服务令牌"
            placeholder="运行 uv run morimemory token"
            variant="outlined"
            density="comfortable"
            prepend-inner-icon="mdi-key-outline"
            hide-details
            class="mb-3"
          />
          <v-text-field
            v-model="userId"
            label="用户 ID"
            variant="outlined"
            density="comfortable"
            prepend-inner-icon="mdi-account-outline"
            hide-details
            class="mb-4"
          />
          <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-3">
            {{ error }}
          </v-alert>
          <v-btn color="primary" size="large" block :loading="connecting" @click="connect">
            连接记忆引擎
          </v-btn>
          <div class="connect-card__hint">
            <code>uv run morimemory init</code>
            <span>→</span>
            <code>uv run morimemory token</code>
            <span>→</span>
            <code>uv run morimemory serve</code>
          </div>
        </div>

        <MemoryExplorer
          v-else-if="client"
          :client="client"
          title="记忆探索器"
          :initial-view="initialView"
        />
      </div>
    </v-main>

    <v-dialog v-model="settingsOpen" max-width="520">
      <v-card class="pa-2">
        <v-card-title>连接设置</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="draft.token"
            label="服务令牌"
            variant="outlined"
            density="comfortable"
            prepend-inner-icon="mdi-key-outline"
          />
          <v-text-field
            v-model="draft.userId"
            label="用户 ID"
            variant="outlined"
            density="comfortable"
            prepend-inner-icon="mdi-account-outline"
          />
          <v-alert v-if="error" type="error" variant="tonal" density="compact">
            {{ error }}
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-btn v-if="connected" variant="text" color="error" @click="disconnect(); settingsOpen = false">
            断开连接
          </v-btn>
          <v-spacer />
          <v-btn variant="text" @click="settingsOpen = false">取消</v-btn>
          <v-btn color="primary" variant="flat" @click="saveSettings">保存并连接</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-app>
</template>

<style scoped>
.dev-drawer {
  border-right: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  background: rgb(var(--v-theme-surface)) !important;
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 22px 20px 28px;
}

.brand__mark {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border: 1px solid rgba(var(--v-theme-primary), 0.32);
  border-radius: 12px;
  background: linear-gradient(
    145deg,
    rgba(var(--v-theme-primary), 0.19),
    rgba(var(--v-theme-primary), 0.02)
  );
  color: rgb(var(--v-theme-primary));
}

.brand strong {
  display: block;
  color: rgb(var(--v-theme-on-surface));
  font-size: 14px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

.brand small {
  color: rgba(var(--v-theme-on-surface), 0.46);
  font-size: 9.5px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.nav-label {
  margin: 0 20px 8px;
  color: rgba(var(--v-theme-on-surface), 0.42);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.nav {
  display: grid;
  gap: 3px;
  padding: 0 12px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 0 11px;
  border-radius: 11px;
  color: rgba(var(--v-theme-on-surface), 0.56);
  font-size: 12.5px;
  text-decoration: none;
}

.nav-item--active {
  background: rgba(var(--v-theme-primary), 0.09);
  color: rgb(var(--v-theme-on-surface));
  font-weight: 600;
}

.nav-item--active :deep(.v-icon) {
  color: rgb(var(--v-theme-primary));
}

.nav-item--disabled {
  opacity: 0.48;
}

.nav-item em {
  margin-left: auto;
  padding: 1px 5px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 999px;
  font-size: 8px;
  font-style: normal;
}

.drawer-spacer {
  flex: 1;
}

.runtime {
  margin: 14px;
  padding: 13px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 13px;
  background: rgb(var(--v-theme-surface-variant));
}

.runtime__head {
  display: flex;
  align-items: center;
  gap: 7px;
  color: rgba(var(--v-theme-on-surface), 0.56);
  font-size: 10.5px;
}

.runtime__head i {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: rgba(var(--v-theme-on-surface), 0.32);
}

.runtime__head i.live {
  background: rgb(var(--v-theme-primary));
  box-shadow: 0 0 0 4px rgba(var(--v-theme-primary), 0.12);
}

.runtime__head strong {
  margin-left: auto;
  color: rgb(var(--v-theme-on-surface));
  font-size: 10px;
}

.runtime__meta {
  display: flex;
  justify-content: space-between;
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.07);
  color: rgba(var(--v-theme-on-surface), 0.44);
  font-size: 9.5px;
}

.dev-appbar {
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  background: rgba(var(--v-theme-surface), 0.88) !important;
  backdrop-filter: blur(16px);
}

.appbar-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.appbar-title span {
  color: rgb(var(--v-theme-primary));
  font-size: 8.5px;
  font-weight: 700;
  letter-spacing: 0.16em;
}

.appbar-title strong {
  color: rgb(var(--v-theme-on-surface));
  font-size: 14px;
  font-weight: 600;
}

.dev-main {
  background: rgb(var(--v-theme-background));
}

.dev-content {
  max-width: 1680px;
  margin: 0 auto;
  padding: 20px 26px 34px;
}

.connect-card {
  max-width: 560px;
  margin: 8vh auto 0;
  padding: 34px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 22px;
  background:
    radial-gradient(
      circle at 100% 0,
      rgba(var(--v-theme-primary), 0.08),
      transparent 55%
    ),
    rgb(var(--v-theme-surface));
  box-shadow: 0 30px 90px rgba(0, 0, 0, 0.28);
}

.connect-card__icon {
  display: grid;
  width: 62px;
  height: 62px;
  place-items: center;
  margin-bottom: 18px;
  border: 1px solid rgba(var(--v-theme-primary), 0.24);
  border-radius: 18px;
  background: rgba(var(--v-theme-primary), 0.08);
}

.connect-card h1 {
  margin: 0 0 8px;
  color: rgb(var(--v-theme-on-surface));
  font-size: 24px;
  font-weight: 680;
  letter-spacing: -0.03em;
}

.connect-card p {
  margin: 0 0 22px;
  color: rgba(var(--v-theme-on-surface), 0.56);
  font-size: 12px;
  line-height: 1.65;
}

.connect-card code {
  padding: 2px 5px;
  border-radius: 5px;
  background: rgba(var(--v-theme-primary), 0.09);
  color: rgb(var(--v-theme-primary));
  font-size: 10.5px;
}

.connect-card__hint {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 18px;
  color: rgba(var(--v-theme-on-surface), 0.38);
  font-size: 10px;
}

@media (max-width: 900px) {
  .dev-content {
    padding: 14px;
  }
}
</style>
