# Source

This bundled plugin is based on the official Wind AIFin Market repository:

- Repository: https://github.com/Wind-Information-Co-Ltd/wind-skills
- Commit: `94c00f94a3b6e8b61ebf375ad9c5cb87da34cd12`
- Commit date: 2026-07-20
- Snapshot: all 78 directories below upstream `skills/`
- Copyright: © Wind AIFinMarket 2026
- Redistribution: confirmed by the user for this Cowork integration

## Cowork adaptations

- Added one default-off `wind-aifinmarket` capability-center entry.
- Added allowlisted sub-skill search and progressive loading.
- Replaced installation and configuration flows with Cowork-managed configuration.
- Restricted runtime execution to eight declared script entries.
- Disabled runtime self-update for Cowork-managed execution.
- Required generated files and Alice downloads to remain in the active Cowork workspace.
- Added credential-safe submit, start, run, finish, and error diagnostics.
- Kept the upstream data-source choice for each sub-skill.

The bundled snapshot does not update itself. A future refresh must pin a new upstream commit,
review the diff, repeat Cowork adaptation, and pass the plugin test suite.

## Frozen sub-skill directory list

- `a-share-primary-theme-identification`
- `add_to_winner_decision_skill`
- `after_close_watchlist_recap_skill`
- `avatar-charlie-munger-thinking`
- `avatar-nassim-taleb-risk`
- `avatar-naval-ravikant-thinking`
- `avatar-warren-buffett-investing`
- `backtest-expert`
- `breakout_candidate_finder_skill`
- `breakout_trade_execution_skill`
- `bull_bear_case_builder_skill`
- `business_model_decoder_skill`
- `buyback_program_reviewer_skill`
- `canslim_growth_scan_skill`
- `conference_call_takeaway_skill`
- `daily_watchlist_morning_brief_skill`
- `dcf-model`
- `dip_buy_decision_skill`
- `dividend_change_explainer_skill`
- `dividend_growth_entry_skill`
- `earnings-analysis`
- `earnings_calendar_planner_skill`
- `earnings_momentum_setup_skill`
- `earnings_preview_skill`
- `earnings_reaction_interpreter_skill`
- `equity-investment-thesis`
- `failed_breakout_exit_skill`
- `gap_open_interpreter_skill`
- `growth_quality_check_skill`
- `guidance_change_impact_skill`
- `high_quality_compounder_finder_skill`
- `hot_stock_quick_read_skill`
- `industry_chain_signal_skill`
- `institutional_position_shift_skill`
- `intraday_abnormal_move_alert_skill`
- `macro_event_market_impact_skill`
- `major_announcement_impact_skill`
- `management_quality_check_skill`
- `market_breadth_health_skill`
- `market-environment-analysis`
- `market_regime_switch_skill`
- `market_sentiment_temperature_skill`
- `moat_strength_review_skill`
- `northbound_capital_flow_skill`
- `pead_opportunity_skill`
- `peer_comparison_decision_skill`
- `policy_headline_interpreter_skill`
- `position-sizer`
- `position_sizing_decision_skill`
- `post-market-debrief`
- `premarket_trade_checklist_skill`
- `price_target_reach_alert_skill`
- `pullback_opportunity_finder_skill`
- `sec_filing_question_answer_skill`
- `sector_rotation_radar_skill`
- `shareholder_letter_digest_skill`
- `stock_first_look_skill`
- `stock_research_memo_writer_skill`
- `stop_loss_discipline_skill`
- `support_break_warning_skill`
- `take_profit_ladder_skill`
- `theme-detector`
- `theme_heat_tracker_skill`
- `theme_leader_identification_skill`
- `trade_plan_builder_skill`
- `trading_halt_resume_tracker_skill`
- `trim_or_hold_decision_skill`
- `turnaround_story_validation_skill`
- `tushare-finance-skill`
- `valuation-pricing-framework`
- `valuation_snapshot_skill`
- `value_dividend_candidate_skill`
- `vcp_breakout_scan_skill`
- `volume_spike_reasoning_skill`
- `watchlist_news_impact_digest_skill`
- `wind-alice`
- `wind-find-finance-skill`
- `wind-mcp-skill`
