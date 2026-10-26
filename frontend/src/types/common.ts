/** 工作台只读接口公共契约（需求第8节信封）。字段冻结于 26-05；
 * 金额/价格/数量一律为字符串（Decimal 序列化），null 与 0 含义不同。 */

export type DataStatus = 'ok' | 'partial' | 'stale' | 'unavailable' | 'error'

/** 统一响应信封：快照而非推送承诺。 */
export interface Envelope<T> {
  schema_version: 1
  account_alias: string
  as_of: string
  generated_at: string
  data_status: DataStatus
  source: string
  age_seconds: number | null
  stale_after_seconds: number | null
  partial: boolean
  missing_reasons: string[]
  data: T
}

/** 运行/数据能力声明（是否可用由后端决定，前端不得臆造）。 */
export interface Capabilities {
  intraday_bars: boolean
  daily_bars: boolean
  account_series: boolean
  controls_available: boolean
}

/** 通用带时间与状态的块：一块失败不影响其他块。 */
export interface Block<T> {
  status: DataStatus
  as_of: string | null
  age_seconds: number | null
  stale_after_seconds: number | null
  missing_reasons: string[]
  data: T | null
}
