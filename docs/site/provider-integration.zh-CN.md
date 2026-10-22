# 接入新的设备来源

Provider 负责连接设备服务与 Mirobody。开发前，先确认现有集成或文件导入能否满足需求。

## 实现约定

将数据解码与网络请求分开：解码器把厂商数据转换成支持的指标，provider 负责认证、请求、
回调和同步。核心方法为：

- `format_data(self, fmt_input: FormatDataInput) -> StandardPulseData`
- `_validate_credentials(self, credentials: dict)`

通过配置系统读取参数；缺少必需凭据的可选集成，不应注册为可用服务。
观测写入使用已有的收集入口，单位转换以指标目录中的标准单位为准。

## 注册与验证

在 `PROVIDER_DIRS` 中加入自己的目录，并保留默认项：

```yaml
PROVIDER_DIRS:
  - mirobody/collect/providers
  - my_providers
```

也可以通过已安装包的 `mirobody.providers` 入口点注册。
连接真实账号前，用合成数据检查单位、时间戳、缺失字段、重复投递和认证失败的处理。
日志不得包含读数或凭据。

完整类示例、OAuth、Webhook 和解码测试见[开发参考](../provider-guide.md)。
现成集成的配置方法见[设备接入](../provider-setup.zh-CN.md)。
