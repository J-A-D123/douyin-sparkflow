# 网页回执格式与验证范围

发送观察器只读取正常网页发起的响应，不生成、重放或修改私信请求，不改变平台业务结果。

2026-11-02 只读诊断发现，聊天历史接口使用 `application/x-protobuf`。原观察器将二进制按 UTF-8 JSON 处理，会丢失真实的业务状态。支持此格式不意味着可忽略平台拒绝或验证码。

字段编号核对来源为本机实际网页加载的公开前端 SDK：

- URL：`https://lf-fe-creator.douyinstatic.com/obj/douyn-creator-scm-cdn/douyin-creator-mono-pc-data/static/js/async/786.15f09b02.js`
- SHA-256：`407bc81a526d3f2d9ef1ba77ff90f308217a4f6883803986a55cc5b97f6ace17`
- `Response`：1 cmd，2 sequence_id，3 status_code，4 error_desc，6 body。
- `ResponseBody`：100 send_message_body。
- `SendMessageResponseBody`：1 server_message_id，3 status，4 client_message_id，5 check_code，6 check_message。

SDK 给省略的状态整数定义零默认值。解码仍要求明确的 cmd=100、嵌套发送结果和非零服务器消息编号，拒绝畸形、重复关键字段、截断、超限或未知格式。外层状态、发送状态和检查码必须通过，不能仅靠 HTTP 200。

发送状态 1 至 5 分别表示不在会话、会话检查未通过、消息检查未通过、检查未通过但仅自己可见、对方限制。这些情况都不能标记为成功，不能靠切换代理、重复发送或忽略检查来处理。

源码只包含最小响应解码器和合成测试字节，不包含 SDK、真实响应、聊天内容、Cookie 或认证头。网页改版后必须再次核对格式，未知响应继续留待核验。服务器接收证据仍不能替代双方 App 收件与火花验收。

普通聊天还要求在全新浏览器上下文中读取服务器记录，支持 cmd=301（单会话）、2043（初始同步）、2048（增量同步）。记录按服务器消息编号精确匹配，再核对正文、`req_user_id` 对应的己方身份、创建时间，并拒绝 `s:visible` 限制、已观察到的限制回调 8101，以及明确非零的 `s:send_response_status` / `s:send_response_check_code`。读取上下文拦截发送请求；不会用旧气泡或本地乐观缓存作为确认依据。

`im_callback_status_code` 是历史消息的扩展字段，不等同于发送响应的 `status` 或 `check_code`。普通网页 SDK 的发送处理读取后两项；本机一次业务状态全零、本人确认 App 收到的消息，其历史扩展值为 21100。该值的完整语义尚无核实说明，因此原样保存，不伪造为零，也不把所有未映射扩展值都解释为发送拒绝。网页确认仍严格依赖明确成功回执与独立历史核对；App 收件另行确认。

普通网页 SDK 的处理路径另核对于[网页实际加载的公开资源](https://lf3-social.iesdouyin.com/obj/douyin-social-cdn/pcim/static/js/async/__federation_expose_default_export.20bef034.js)，SHA-256 为 `7f759f84330f7352a9b8ef36856d47294f89724ecd5b49c282830dd30472d861`。只记录来源与字段，不将该资源复制到源码包。
