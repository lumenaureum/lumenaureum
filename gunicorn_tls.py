import ssl

def ssl_context(config, default_ssl_context_factory):
    ctx = default_ssl_context_factory()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx
