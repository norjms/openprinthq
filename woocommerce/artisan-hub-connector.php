<?php
/**
 * Plugin Name: OpenPrintHQ connector
 * Description: Glue between WooCommerce and OpenPrintHQ: lets Woo fetch product images from and deliver webhooks to the hub on a private network, shows hub links on products, and re-checks supply availability at checkout. Install as a must-use plugin and set ARTISAN_HUB_HOST in wp-config.php, for example define('ARTISAN_HUB_HOST', 'hub.example.com');
 */

if (!defined('ABSPATH')) {
    exit;
}

if (!defined('ARTISAN_HUB_HOST')) {
    define('ARTISAN_HUB_HOST', ''); // set in wp-config.php: the hub's host name
}

/* --------------------------------------------------------------------------
 * The hub may resolve to a private network address. WordPress
 * refuses private addresses for media sideloads and webhook delivery, so
 * trust exactly this one host (nothing else changes).
 * ------------------------------------------------------------------------ */
add_filter('http_request_host_is_external', function ($external, $host) {
    return ($host === ARTISAN_HUB_HOST) ? true : $external;
}, 10, 2);

/* --------------------------------------------- Hub link on the product screen */
add_action('add_meta_boxes', function () {
    add_meta_box('openprinthq', 'OpenPrintHQ', function ($post) {
        $url = get_post_meta($post->ID, '_artisan_hub_url', true);
        $code = get_post_meta($post->ID, '_artisan_hub_model_code', true);
        if ($url) {
            echo '<p>Managed by OpenPrintHQ' . ($code ? ' (' . esc_html($code) . ')' : '') . '.</p>';
            echo '<p>Price, photos, colors and stock are pushed from the hub; edit them there so they stay in sync.</p>';
            echo '<p><a class="button button-primary" target="_blank" href="' . esc_url($url) . '">Open in OpenPrintHQ</a></p>';
        } else {
            echo '<p>Not linked to OpenPrintHQ.</p>';
        }
    }, 'product', 'side', 'high');
});

/* ------------------------------------------------ Checkout supply re-check
 * Stock quantities are already driven by the hub. This is a second line of
 * defence for the moment between a supply change and the next stock push.
 * Fails open (allows the order) if the hub cannot be reached, because Woo's
 * own stock levels still apply.
 * ---------------------------------------------------------------------- */
function artisan_hub_check_cart() {
    $key = get_option('artisan_hub_api_key');
    if (!$key || !function_exists('WC') || !WC()->cart) {
        return;
    }
    $want = array();
    foreach (WC()->cart->get_cart() as $item) {
        $product = $item['data'];
        if (!$product) {
            continue;
        }
        $sku = $product->get_sku();
        if ($sku) {
            $want[$sku] = ($want[$sku] ?? 0) + (int) $item['quantity'];
        }
    }
    if (!$want) {
        return;
    }
    // The cart is checked as a whole (qty in the same order as sku): two products that share a scarce part
    // can't both claim it.
    $resp = wp_safe_remote_get('https://' . ARTISAN_HUB_HOST . '/api/v1/availability?sku=' . rawurlencode(implode(',', array_keys($want)))
        . '&qty=' . rawurlencode(implode(',', array_values($want))), array(
        'timeout' => 4,
        'headers' => array('Authorization' => 'Bearer ' . $key),
    ));
    if (is_wp_error($resp) || wp_remote_retrieve_response_code($resp) !== 200) {
        return;
    }
    $data = json_decode(wp_remote_retrieve_body($resp), true);
    if (!is_array($data) || empty($data['items'])) {
        return;
    }
    foreach ($want as $sku => $qty) {
        if (!array_key_exists($sku, $data['items']) || $data['items'][$sku] === null) {
            continue;
        }
        $can = (int) $data['items'][$sku];
        if ($qty > $can) {
            wc_add_notice(sprintf(
                'Sorry, we can only make %d more of "%s" right now. Please lower the quantity.',
                max(0, $can),
                esc_html($data['names'][$sku] ?? $sku)
            ), 'error');
        }
    }
}
add_action('woocommerce_check_cart_items', 'artisan_hub_check_cart');
add_action('woocommerce_after_checkout_validation', function () {
    artisan_hub_check_cart();
}, 20);

/* ------------------------------------------- Media cleanup on product delete
 * When the hub permanently deletes a product it manages, also delete the
 * photos the hub sideloaded for it (featured, gallery, variation images),
 * unless another product still uses the same attachment.
 * ---------------------------------------------------------------------- */
function artisan_hub_media_in_use($attachment_id, $exclude_ids) {
    global $wpdb;
    $exclude = implode(',', array_map('intval', $exclude_ids));
    $thumb = (int) $wpdb->get_var($wpdb->prepare(
        "SELECT COUNT(*) FROM {$wpdb->postmeta} WHERE meta_key = '_thumbnail_id' AND meta_value = %s AND post_id NOT IN ($exclude)",
        (string) $attachment_id
    ));
    if ($thumb > 0) {
        return true;
    }
    $like = '%' . $wpdb->esc_like((string) $attachment_id) . '%';
    $rows = $wpdb->get_col($wpdb->prepare(
        "SELECT meta_value FROM {$wpdb->postmeta} WHERE meta_key = '_product_image_gallery' AND meta_value LIKE %s AND post_id NOT IN ($exclude)",
        $like
    ));
    foreach ($rows as $v) {
        if (in_array((string) $attachment_id, explode(',', $v), true)) {
            return true;
        }
    }
    return false;
}

add_action('before_delete_post', function ($post_id) {
    if (get_post_type($post_id) !== 'product' || !get_post_meta($post_id, '_artisan_hub_url', true)) {
        return;
    }
    $ids = array();
    $owners = array((int) $post_id);
    $thumb = (int) get_post_thumbnail_id($post_id);
    if ($thumb) {
        $ids[] = $thumb;
    }
    $gallery = get_post_meta($post_id, '_product_image_gallery', true);
    if ($gallery) {
        $ids = array_merge($ids, array_map('intval', explode(',', $gallery)));
    }
    foreach (get_children(array('post_parent' => $post_id, 'post_type' => 'product_variation', 'fields' => 'ids')) as $vid) {
        $owners[] = (int) $vid;
        $vt = (int) get_post_thumbnail_id($vid);
        if ($vt) {
            $ids[] = $vt;
        }
    }
    foreach (get_attached_media('image', $post_id) as $att) {
        $ids[] = (int) $att->ID;
    }
    $ids = array_unique(array_filter($ids));
    // run after the product (and its variations) are gone so their own meta no longer counts
    add_action('deleted_post', function ($deleted) use ($post_id, $ids, $owners) {
        if ((int) $deleted !== (int) $post_id) {
            return;
        }
        foreach ($ids as $aid) {
            if (get_post_type($aid) === 'attachment' && !artisan_hub_media_in_use($aid, $owners)) {
                wp_delete_attachment($aid, true);
            }
        }
    });
}, 10, 1);
