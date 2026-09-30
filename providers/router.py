from providers.instagram.provider import InstagramProvider
from providers.instagram.story_provider import InstagramStoryProvider
from providers.base import UnsupportedUrl

class ProviderRouter:
    def __init__(self,pool=None):
        self.providers=[]
        if pool is not None:
            self.providers.append(InstagramStoryProvider(pool))
        self.providers.append(InstagramProvider(pool))

    def for_url(self,url):
        for provider in self.providers:
            if provider.supports(url): return provider
        raise UnsupportedUrl("Unsupported social media URL")
