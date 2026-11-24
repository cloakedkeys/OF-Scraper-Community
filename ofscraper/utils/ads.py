def get_ad_key_words():
    # A hashtag counts only as a whole tag: "#ad" but not "#adult" or
    # "#adorable". "trial" was dropped as "discount" and "limited time" were:
    # creators end ordinary posts with "free trial" links to their own page.
    return r"(#(?:ad|ads|AD|advertising|sponsored|promotion)\b|giveaway|\Buser\s+promo|shoutout|endorsement)"
