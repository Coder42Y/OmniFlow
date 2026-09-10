export const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
export const time = '2026-09-08T08:00:00Z';
export const user = {
  id: id(1),
  username: 'synthetic_creator',
  role: 'user',
  status: 'active',
  created_at: time,
};
export const policy = {
  username_pattern: '^[a-z0-9_]{3,32}$',
  username_normalization: 'trim_ascii_spaces_then_lowercase',
  password_min_length: 14,
  password_max_length: 100,
  session_ttl_seconds: 604800,
  invitation_ttl_default_seconds: 604800,
  password_reset_ttl_default_seconds: 1800,
};
export const capabilities = {
  text: {
    model: 'gemini-3.8-flash-low',
    availability: { available: false, reason: 'PROVIDER_UNAVAILABLE' },
  },
  image: {
    model: 'agnes-image-2.5-flash',
    ratios: ['1:1', '9:16', '16:9'],
    size_tiers: ['1K', '2K'],
    reference_editing_enabled: true,
    availability: { available: true, reason: null },
  },
  ai_video: {
    model: 'agnes-video-2.5-flash',
    modes: ['keyframe', 'text'],
    ratios: ['9:16', '16:9'],
    min_seconds: 4,
    max_seconds: 12,
    size_tiers: ['720P'],
    availability: { available: true, reason: null },
  },
  local_motion: { available: true, reason: null },
  business_quotas_enabled: false,
  limits: {
    max_message_chars: 16000,
    max_upload_bytes: 1048576,
    max_image_pixels: 4000000,
    max_message_attachments: 8,
    max_image_references: 5,
    max_page_size: 100,
  },
};
export const conversation = (n = 2) => ({
  id: id(n),
  title: '合成创作对话 ' + n,
  created_at: time,
  updated_at: time,
  last_event_id: '0',
  active_run_id: null,
});
export const message = (n, role = 'user', cid = id(2)) => ({
  id: id(100 + n),
  conversation_id: cid,
  seq: n,
  role,
  content:
    role === 'user'
      ? `第 ${Math.ceil(n / 2)} 轮想法：冷白城市与暖色灯光。`
      : '这是合成测试正文，不代表真实模型响应。\n'.repeat(4),
  status: 'completed',
  run_id: id(1000 + Math.ceil(n / 2)),
  client_message_id: role === 'user' ? id(100 + n) : null,
  attachment_version_ids: [],
  selected_version_id: null,
  artifact_version_ids: [],
  created_at: time,
  updated_at: time,
});
export const snapshot = (count = 0, cid = id(2)) => ({
  conversation: { ...conversation(), id: cid },
  messages: Array.from({ length: count * 2 }, (_, i) =>
    message(i + 1, i % 2 ? 'assistant' : 'user', cid),
  ),
  messages_next_cursor: null,
  runs: [],
  tasks: [],
  artifact_versions: [],
  last_event_id: '0',
});
export const version = (engine = null) => ({
  id: id(6),
  artifact_id: id(5),
  version_number: 1,
  parent_version_id: null,
  source_task_id: null,
  execution_engine: engine,
  media_type: engine ? 'video/mp4' : 'image/png',
  byte_size: 72,
  sha256: '0'.repeat(64),
  width: 32,
  height: 48,
  duration_seconds: engine ? 1.25 : null,
  fps: engine ? 12 : null,
  created_at: time,
  content_url: `/api/v1/artifacts/${id(5)}/versions/${id(6)}/content`,
});
export const artifact = () => ({
  id: id(5),
  kind: 'image',
  title: '合成视觉稿',
  current_version_id: id(6),
  version_count: 1,
  created_at: time,
  updated_at: time,
});
export const task = (status = 'queued') => ({
  id: id(8),
  conversation_id: id(2),
  run_id: null,
  kind: 'ai_video',
  status,
  requested_parameters: {
    kind: 'ai_video',
    conversation_id: id(2),
    mode: 'text',
    prompt: '合成要求',
    seconds: 4,
    size_tier: '720P',
    aspect_ratio: '9:16',
  },
  execution_engine: 'agnes-video-2.5-flash',
  output_version_ids: [],
  can_cancel: status === 'queued',
  can_recover: status === 'saving',
  error: null,
  created_at: time,
  updated_at: time,
});
export const event = (type, data, seq = '1') => ({
  event: type,
  id: seq,
  data: { event_id: seq, conversation_id: id(2), type, occurred_at: time, data },
});
export const json = (data) =>
  new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
