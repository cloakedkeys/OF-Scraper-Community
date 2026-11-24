def get_ad_key_words():
    # A hashtag counts only as a whole tag: "#ad" but not "#adult" or
    # "#adorable". "trial" was dropped as "discount" and "limited time" were:
    # creators end ordinary posts with "free trial" links to their own page.
    # Keep the pattern all lower-case: post_neg_text_filter matches a
    # lower-case pattern in any case, and any capital (as "AD" or "\B" were)
    # makes it case-sensitive, letting "#Ad" or "Giveaway" through.
    return r"(#(?:ad|ads|advertising|sponsored|promotion)\b|giveaway|user\s+promo|shoutout|endorsement)"
