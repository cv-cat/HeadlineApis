# Headline article draft capture (2026-10-11)

This note records a real creator-web draft save and cleanup. It intentionally omits cookie values, anti-token values, dynamic query values, account identifiers, and the draft body.

## Save request

- `POST /mp/agw/article/publish`
- Query parameter names: `source`, `type`, `aid`, `mp_publish_ab_val`, dynamic `msToken`, and dynamic `a_bogus`.
- Request header names relevant to the replay boundary: `content-type: application/x-www-form-urlencoded;charset=UTF-8`, `Origin`, `Referer`, `tt-anti-token`, and `x-secsdk-csrf-token`.
- Observed form field names:

  `source`, `extra`, `content`, `title`, `search_creation_info`, `title_id`, `mp_editor_stat`, `is_refute_rumor`, `save`, `entrance`, `timer_status`, `timer_time`, `educluecard`, `draft_form_data`, `pgc_feed_covers`, `article_ad_type`, `is_fans_article`, `govern_forward`, `praise`, `disable_praise`, `tree_plan_article`, `star_order_id`, `star_order_name`, `customer_nick_name`, `activity_tag`, `trends_writing_tag`, `claim_exclusive`.

The first save request returned HTTP 200, `save=0`, JSON `code=0`, `err_no=0`, message `保存成功`, and data keys `pgc_feed_covers` and `pgc_id`. Later autosave requests used the same route and field family; one carried a non-empty title and content. The exact editor serialization and signature generation are not treated as a stable independent-client contract.

## Cleanup request

- `POST /mp/agw/creator_center/delete_draft`
- JSON shape: `{"drafts":[{"draft_type":2,"gid":"<selected draft>"}]}`
- Response: HTTP 200, `code=0`, `message=success`.
- The temporary draft disappeared from the creator draft list after deletion; the pre-existing draft remained.

## Client boundary

`TouTiaoCreatorWebApi.delete_draft()` wraps the explicit delete contract. The repository does not fabricate the creator page's `msToken`, `a_bogus`, anti-token, or editor serialization, and it does not expose a public article publish method based on this autosave observation. Public video publishing remains a separate, already documented browser acceptance path.
