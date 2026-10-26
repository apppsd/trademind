/** 标的身份与目录类型（26-04 冻结）。内部唯一键始终是 instrument_id；
 * 中文名只是展示别名，不参与 URL、请求或订单关联。 */

export interface InstrumentInfo {
  instrument_id: string
  /** 展示简称，如“中概互联ETF”；未知时为 null（前端显示“名称待补充”+代码）。 */
  display_name: string | null
  /** 白名单登记的完整基金名称。 */
  full_name: string | null
  market: string | null
  kind: string | null
  currency: string | null
  /** 名称来源：whitelist=已核验白名单；unknown=无登记。 */
  name_source: 'whitelist' | 'unknown'
  aliases: string[]
  purchase_check?: { status: string; reasons: string[]; one_lot_cost?: string | null }
  capabilities: {
    tradable: boolean
    settlement_rule: string | null
    lot_size: number | null
    intraday_bars: boolean
    daily_bars: boolean
  }
}
