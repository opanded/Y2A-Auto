"""任务列表的视图模型：把「什么状态该显示什么」从模板里搬出来。

背景
----
任务列表同时有两套模板：
  - partials/task_row.html  桌面表格行
  - partials/task_card.html 窄屏卡片
这两套各自手写过一遍「按 status 决定按钮」和「字幕异常提示」的逻辑。
同一个改动要改两处，实测已经漏过两次（pending 冷却中的手动上传、
downloaded 的上传按钮都只在表格版修了）。

这里把判断收敛成唯一事实来源：模板只负责按返回的描述渲染，
不再自己 if/elif 状态。markup 差异（行内按钮 vs 整宽按钮）由模板决定。

设计约束
--------
- 不生成 URL：只返回 endpoint + kwargs，交给模板的 url_for，
  这样模块可以脱离 Flask 请求上下文单测。
- 返回纯 dict / list，方便断言。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 状态分组
# ---------------------------------------------------------------------------

# 正在处理（可中止）。downloaded 不在其中——它已可手动上传，单独处理。
ACTIVE_PROCESSING_STATES = frozenset({
    'downloading', 'asr_transcribing', 'translating_subtitle', 'encoding_video',
    'translating', 'tagging', 'partitioning', 'moderating', 'uploading',
    'fetching_info', 'info_fetched',
})

_STATUS_ICONS = {
    'downloading': 'download',
    'downloaded': 'download',
    'uploading': 'cloud-upload',
    'translating': 'translate',
    'translating_subtitle': 'translate',
    'asr_transcribing': 'mic',
    'encoding_video': 'cpu',
    'awaiting_manual_review': 'eye',
    'completed': 'check-circle',
    'failed': 'x-circle',
    'ready_for_upload': 'cloud-arrow-up',
    'pending': 'pause-circle',
    'tagging': 'tags',
    'partitioning': 'diagram-3',
    'moderating': 'shield-check',
    'info_fetched': 'info-circle',
    'fetching_info': 'info-circle',
}


def task_status_icon(status) -> str:
    """状态 → bootstrap-icons 名称。未知状态回退到 circle。"""
    return _STATUS_ICONS.get(str(status or ''), 'circle')


# ---------------------------------------------------------------------------
# 失败归类（原先在 app.py，挪到这里让视图模型自洽、避免循环导入）
# ---------------------------------------------------------------------------

def task_failure_category(task):
    """把失败原因归类，供界面提示「重试是否有用」。

    返回 (阶段标签, 是否可重试, 颜色)。
    """
    msg = str((task or {}).get('error_message') or '')
    cat = str((task or {}).get('error_category') or '')
    if not msg and not cat:
        return ('', True, 'secondary')

    if cat == 'user_aborted' or '用户主动中止' in msg or '用户主动放弃' in msg:
        return ('人工中止', False, 'secondary')
    if 'not available in your count' in msg or '地区限制' in msg or 'geo' in cat:
        return ('地区限制', False, 'secondary')
    if 'this video is private' in msg or 'has been removed' in msg or 'unavailable' in msg:
        return ('视频不可用', False, 'secondary')
    if '下载' in msg:
        return ('下载失败', True, 'warning')
    if '翻译' in msg or '元数据' in msg or '简介' in msg:
        return ('AI 处理失败', True, 'warning')
    if '137022' in msg or '投稿过于频繁' in msg or '限流' in msg:
        return ('平台限流', True, 'warning')
    if '-352' in msg or '603' in msg or '风控' in msg:
        return ('账号风控', True, 'danger')
    if '上传' in msg:
        return ('上传失败', True, 'danger')
    return ('未知原因', True, 'danger')


# ---------------------------------------------------------------------------
# 操作按钮
# ---------------------------------------------------------------------------

def _target_label(target: str) -> str:
    return '双平台' if target == 'both' else ('bilibili' if target == 'bilibili' else 'AcFun')


def build_task_actions(task: Dict[str, Any], target: Optional[str] = None) -> List[Dict[str, Any]]:
    """返回该任务当前可执行的操作列表（唯一事实来源）。

    每项形如：
        {
          'kind': 'form' | 'link' | 'disabled',
          'endpoint': 'force_upload_task_route',   # form/link 用
          'kwargs': {'task_id': '...'},
          'extra_fields': {'delete_files': 'false'},   # form 隐藏域
          'label': '上传',
          'icon': 'cloud-upload',
          'css': 'btn-success',
          'title': '悬浮提示',
          'confirm': '点击前确认文案',
          'note': '19:30 将自动重试',   # 可选：按钮下方的小字说明
        }
    """
    task = task or {}
    status = str(task.get('status') or '')
    task_id = task.get('id') or ''
    target = str(target or task.get('upload_target') or 'acfun').lower()
    retry_after = str(task.get('retry_after') or '').strip()

    actions: List[Dict[str, Any]] = []

    def _add(**kw):
        base = {
            'kind': 'form',
            'endpoint': '',
            'kwargs': {},
            'extra_fields': {},
            'label': '',
            'icon': 'circle',
            'css': 'btn-secondary',
            'title': '',
            'confirm': '',
            'note': '',
        }
        base.update(kw)
        actions.append(base)

    def form(endpoint, label, icon, css, *, title='', confirm='', extra=None, note=''):
        _add(kind='form', endpoint=endpoint, kwargs={'task_id': task_id}, label=label,
             icon=icon, css=css, title=title, confirm=confirm,
             extra_fields=extra or {}, note=note)

    def link(endpoint, label, icon, css, *, title=''):
        _add(kind='link', endpoint=endpoint, kwargs={'task_id': task_id}, label=label,
             icon=icon, css=css, title=title)

    if status == 'downloaded':
        # 素材已落盘，用户点进来就是要能手动投；缺的元数据由后端现补。
        form('force_upload_task_route', '上传', 'cloud-upload', 'btn-success',
             title='素材已下载完成，立即上传（缺少的元数据会自动补齐）')
        form('abandon_task_route', '中止', 'stop-circle', 'btn-outline-secondary',
             confirm='确定中止该任务？将停止当前的处理流程。',
             extra={'delete_files': 'false'})

    elif status in ACTIVE_PROCESSING_STATES:
        form('abandon_task_route', '中止', 'stop-circle', 'btn-outline-secondary',
             confirm='确定中止该任务？将停止当前的下载/处理流程。',
             extra={'delete_files': 'false'})

    elif status == 'awaiting_manual_review':
        link('review_task', '审核', 'eye', 'btn-warning')

    elif status == 'ready_for_upload':
        form('force_upload_task_route', f'上传到{_target_label(target)}',
             'cloud-upload', 'btn-success')

    elif status == 'pending':
        if retry_after:
            # 冷却中仍允许手动投：自动重试只是兜底，不是唯一出路。
            form('force_upload_task_route', '立即上传', 'cloud-upload', 'btn-success',
                 title=f'该任务原定 {retry_after} 自动重试，点此立即上传',
                 note=f'{retry_after[11:16]} 将自动重试')
        else:
            form('start_task_route', '开始', 'play-fill', 'btn-primary')

    elif status == 'failed':
        _, can_retry, _color = task_failure_category(task)
        if can_retry:
            form('start_task_route', '重试', 'arrow-repeat', 'btn-primary')
        else:
            cat = task_failure_category(task)[0]
            _add(kind='disabled', label='不可重试', icon='slash-circle',
                 css='btn-outline-secondary',
                 title=f'{cat}：重试不会成功，请删除或重新添加')

    elif status == 'completed':
        form('force_upload_task_route', '重新上传', 'arrow-clockwise', 'btn-outline-success',
             confirm='该任务已上传成功，确定再次提交？可能产生重复稿件。')

    return actions


# ---------------------------------------------------------------------------
# 字幕异常提示
# ---------------------------------------------------------------------------

_QC_REASON_PREFIX_ZH = {
    'ai_fail': 'AI复核未通过',
    'ai_pass': 'AI复核通过',
    'ai_warn': 'AI提示但规则放行',
    'rule_fail': '规则拦截失败',
    'rule_pass': '规则直接通过',
    'qc_skipped': 'AI不可用，按规则结果处理',
}

_QC_REASON_TOKEN_ZH = {
    'hallucination_meta': '疑似幻觉/元信息污染',
    'healthy_dialogue': '内容正常',
    'borderline_repeat': '疑似重复模板',
    'ultra_short_repeat': '超短字幕重复',
    'ultra_short_low_info': '超短字幕信息量过低',
    'provider_disabled': '未启用AI复核提供商',
    'empty_sample': '抽样为空',
    'asr_no_subtitle': '语音识别未能生成字幕，已跳过字幕流程',
}

_ASR_WARNING_ZH = {
    'vad_low_coverage': 'VAD检测语音占比过低，字幕时间轴可能不准确',
    'asr_no_timestamps': 'ASR返回结果中缺少时间戳，已尝试自动对齐',
    'missing silero-vad or torch dependency':
        '运行环境缺少 silero-vad / torch 依赖，VAD 已跳过并回退到固定分片识别',
    'vad_no_speech': 'VAD未在音频中检测到语音活动',
    'vad_no_usable_window': 'VAD分割的语音片段均不可用',
    'asr_failed': 'ASR语音识别全部失败',
}

_SUBTITLE_WARNING_ZH = {
    'subtitle_embed_failed': '字幕烧录失败，已保留字幕文件并继续上传原视频',
    'asr_failed_block_embed': 'ASR/VAD 质量结局为 failed（来源不可信），已拒绝烧录字幕并继续上传原视频',
    'asr_degraded_block_embed': 'ASR/VAD 质量结局为 degraded 且未通过严格质检，已拒绝烧录字幕',
    'subtitle_qc_rejected': '字幕质检未通过，已拒绝烧录并保留字幕文件供人工检查',
    'qc_unavailable': '字幕质检未执行（文件缺失或执行异常），已按拒绝处理，可关闭字幕质检开关放行',
}


def _qc_reason_text(reason_raw: str) -> str:
    parts = str(reason_raw or '').split(':', 1)
    if len(parts) > 1:
        prefix, token = parts[0], parts[1]
        prefix_zh = _QC_REASON_PREFIX_ZH.get(prefix, '')
    else:
        prefix_zh, token = '', parts[0]
    if token in _QC_REASON_TOKEN_ZH:
        token_zh = _QC_REASON_TOKEN_ZH[token]
    elif token.startswith('ai_error'):
        token_zh = 'AI调用异常'
    else:
        token_zh = token or '字幕质检失败'
    return f'{prefix_zh}（{token_zh}）' if prefix_zh else token_zh


def build_subtitle_warnings(task: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """返回需要显示的「字幕异常」徽章列表：[{label, tooltip, variant}]。

    合并质检失败、ASR 警告、烧录异常三类，模板只需 for 循环渲染。
    """
    task = task or {}
    config = config or {}
    badges: List[Dict[str, str]] = []

    qc_failed = bool(task.get('subtitle_qc_failed'))
    asr_raw = str(task.get('asr_warning_message') or '').strip()
    subtitle_raw = str(task.get('subtitle_warning_message') or '').strip()

    if qc_failed or asr_raw:
        tips: List[str] = []
        if qc_failed:
            try:
                threshold = float(config.get('SUBTITLE_QC_THRESHOLD', 0.35))
            except (TypeError, ValueError):
                threshold = 0.35
            reason = _qc_reason_text(task.get('subtitle_qc_reason') or '字幕质检失败')
            score = task.get('subtitle_qc_score')
            tip = f'原因: {reason}'
            if score is not None and score != '':
                try:
                    tip += f' | 分数: {float(score):.3f}'
                except (TypeError, ValueError):
                    pass
            tip += f' | 阈值: {threshold:.3f}'
            tips.append(tip)
        if asr_raw:
            if asr_raw in _ASR_WARNING_ZH:
                tips.append(_ASR_WARNING_ZH[asr_raw])
            elif asr_raw.startswith('vad_failed'):
                tips.append(f'VAD语音检测处理失败：{asr_raw}')
            else:
                tips.append(f'ASR/VAD警告: {asr_raw}')
        badges.append({
            'label': '字幕异常',
            'tooltip': ' | '.join(tips),
            'variant': 'bg-warning text-dark',
        })

    if subtitle_raw:
        tip = _SUBTITLE_WARNING_ZH.get(subtitle_raw, f'字幕处理警告: {subtitle_raw}')
        badges.append({
            'label': '字幕烧录异常',
            'tooltip': tip,
            'variant': 'bg-warning text-dark',
        })

    return badges


# 模板可能传入 sqlite3.Row、dict，也可能是测试用的「属性式占位对象」。
# dict(obj) 对这种占位对象会去取 __iter__，而它的 __getattr__ 返回 None，
# 于是一个 TypeError: 'NoneType' object is not callable 从最深处冒出来。
# 这里统一收敛：先用 dict() 试，失败就按已知字段名逐个 getattr。
_TASK_FIELDS = (
    'id', 'status', 'upload_target', 'retry_after', 'upload_progress',
    'video_title_original', 'video_title_translated',
    'description_original', 'description_translated',
    'error_message', 'error_category',
    'subtitle_qc_failed', 'subtitle_qc_reason', 'subtitle_qc_score',
    'subtitle_quality_state', 'asr_warning_message', 'subtitle_warning_message',
    'updated_at', 'created_at',
    'selected_partition_id', 'recommended_partition_id',
    'selected_partition_id_acfun', 'recommended_partition_id_acfun',
    'selected_partition_id_bilibili', 'recommended_partition_id_bilibili',
    'tags_generated', 'acfun_upload_response', 'bilibili_upload_response',
    'cover_path_local', 'video_path_local',
)


def _as_mapping(task) -> Dict[str, Any]:
    """把任意任务对象转成 dict，属性式对象也能读。"""
    if task is None:
        return {}
    if isinstance(task, dict):
        return dict(task)
    try:
        return dict(task)
    except (TypeError, ValueError):
        pass
    out: Dict[str, Any] = {}
    for key in _TASK_FIELDS:
        try:
            out[key] = getattr(task, key, None)
        except Exception:
            out[key] = None
    return out


def prepare_task_view(task: Dict[str, Any], config: Optional[Dict[str, Any]] = None,
                      target: Optional[str] = None) -> Dict[str, Any]:
    """给模板用的合并视图：补上派生字段，模板直接读即可。"""
    view = _as_mapping(task)
    status = str(view.get('status') or '')

    view['_status_icon'] = task_status_icon(status)
    view['_actions'] = build_task_actions(view, target=target)
    view['_subtitle_warnings'] = build_subtitle_warnings(view, config)
    view['_display_title'] = (
        view.get('video_title_translated') or view.get('video_title_original') or '未获取标题'
    )
    if status == 'failed':
        cat, can_retry, _color = task_failure_category(view)
        view['_can_retry'] = bool(can_retry)
        view['_failure_category'] = cat
    return view
