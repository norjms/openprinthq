<?php
/**
 * Plugin Name: OpenPrintHQ instant quotes
 * Description: The [artisan_quote] form: a customer uploads their own 3D model, OpenPrintHQ slices and prices it, and the quoted print goes in the cart as the hidden "Custom 3D print" product at the quoted price. The hub does all the work; WordPress only relays (the customer never talks to the hub directly). Needs the OpenPrintHQ connector (artisan_hub_api_key option).
 */

if (!defined('ABSPATH')) {
    exit;
}
if (!defined('ARTISAN_HUB_HOST')) {
    define('ARTISAN_HUB_HOST', ''); // set in wp-config.php: the hub's host name
}

function artisan_quote_hub($method, $path, $args = array()) {
    $key = get_option('artisan_hub_api_key');
    if (!$key) {
        return new WP_Error('artisan_quote', 'Quotes are not set up yet.', array('status' => 503));
    }
    $args = array_merge(array('method' => $method, 'timeout' => 60), $args);
    $args['headers'] = array_merge($args['headers'] ?? array(), array('Authorization' => 'Bearer ' . $key));
    $resp = wp_safe_remote_request('https://' . ARTISAN_HUB_HOST . $path, $args);
    if (is_wp_error($resp)) {
        return new WP_Error('artisan_quote', 'The quote service is not answering. Please try again shortly.', array('status' => 502));
    }
    $code = wp_remote_retrieve_response_code($resp);
    $type = wp_remote_retrieve_header($resp, 'content-type');
    $body = wp_remote_retrieve_body($resp);
    if (strpos((string) $type, 'image/') === 0) {
        return array('_image' => $body, '_type' => $type, '_code' => $code);
    }
    $data = json_decode($body, true);
    if ($code >= 400) {
        $msg = is_array($data) && isset($data['detail']) && is_string($data['detail']) ? $data['detail'] : 'Something went wrong with the quote.';
        return new WP_Error('artisan_quote', $msg, array('status' => $code));
    }
    return $data;
}

function artisan_quote_ip() {
    return isset($_SERVER['HTTP_CF_CONNECTING_IP']) ? sanitize_text_field(wp_unslash($_SERVER['HTTP_CF_CONNECTING_IP']))
        : (isset($_SERVER['REMOTE_ADDR']) ? sanitize_text_field(wp_unslash($_SERVER['REMOTE_ADDR'])) : '');
}

function artisan_quote_token_ok($t) {
    return is_string($t) && preg_match('/^[A-Za-z0-9_-]{16,40}$/', $t);
}

/* ------------------------------------------------------------ REST relay */
add_action('rest_api_init', function () {
    register_rest_route('artisan/v1', '/quote', array(
        'methods' => 'POST',
        'permission_callback' => '__return_true',
        'callback' => function (WP_REST_Request $req) {
            $files = $req->get_file_params();
            if (empty($files['file']) || !empty($files['file']['error']) || !is_uploaded_file($files['file']['tmp_name'])) {
                return new WP_Error('artisan_quote', 'Choose a model file to upload (it may be larger than this site accepts).', array('status' => 400));
            }
            $f = $files['file'];
            $boundary = wp_generate_password(24, false);
            $name = preg_replace('/[^A-Za-z0-9._ -]+/', '_', basename($f['name']));
            $body = "--$boundary\r\nContent-Disposition: form-data; name=\"customer_ip\"\r\n\r\n" . artisan_quote_ip() . "\r\n";
            $body .= "--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"$name\"\r\nContent-Type: application/octet-stream\r\n\r\n";
            $body .= file_get_contents($f['tmp_name']) . "\r\n--$boundary--\r\n";
            return artisan_quote_hub('POST', '/api/store/quotes', array(
                'body' => $body, 'timeout' => 120,
                'headers' => array('Content-Type' => 'multipart/form-data; boundary=' . $boundary),
            ));
        },
    ));
    register_rest_route('artisan/v1', '/quote/(?P<token>[A-Za-z0-9_-]+)', array(
        'methods' => 'GET',
        'permission_callback' => '__return_true',
        'callback' => function (WP_REST_Request $req) {
            $t = $req['token'];
            if (!artisan_quote_token_ok($t)) {
                return new WP_Error('artisan_quote', 'Unknown quote.', array('status' => 404));
            }
            return artisan_quote_hub('GET', '/api/store/quotes/' . $t, array('timeout' => 20));
        },
    ));
    register_rest_route('artisan/v1', '/quote/(?P<token>[A-Za-z0-9_-]+)/choose', array(
        'methods' => 'POST',
        'permission_callback' => '__return_true',
        'callback' => function (WP_REST_Request $req) {
            $t = $req['token'];
            if (!artisan_quote_token_ok($t)) {
                return new WP_Error('artisan_quote', 'Unknown quote.', array('status' => 404));
            }
            $p = $req->get_json_params();
            return artisan_quote_hub('POST', '/api/store/quotes/' . $t . '/choose', array(
                'timeout' => 20, 'headers' => array('Content-Type' => 'application/json'),
                'body' => wp_json_encode(array('material' => (string) ($p['material'] ?? ''), 'colour' => $p['colour'] ?? null,
                                               'quantity' => (int) ($p['quantity'] ?? 1))),
            ));
        },
    ));
    register_rest_route('artisan/v1', '/quote/(?P<token>[A-Za-z0-9_-]+)/preview/(?P<name>preview_plate_[0-9]+\.png)', array(
        'methods' => 'GET',
        'permission_callback' => '__return_true',
        'callback' => function (WP_REST_Request $req) {
            if (!artisan_quote_token_ok($req['token'])) {
                return new WP_Error('artisan_quote', 'Unknown quote.', array('status' => 404));
            }
            $r = artisan_quote_hub('GET', '/api/store/quotes/' . $req['token'] . '/preview/' . $req['name'], array('timeout' => 20));
            if (is_wp_error($r) || empty($r['_image'])) {
                return new WP_Error('artisan_quote', 'No preview.', array('status' => 404));
            }
            header('Content-Type: image/png');
            header('Cache-Control: private, max-age=3600');
            echo $r['_image']; // phpcs:ignore WordPress.Security.EscapeOutput
            exit;
        },
    ));
    register_rest_route('artisan/v1', '/quote/(?P<token>[A-Za-z0-9_-]+)/cart', array(
        'methods' => 'POST',
        'permission_callback' => '__return_true',
        'callback' => function (WP_REST_Request $req) {
            $t = $req['token'];
            if (!artisan_quote_token_ok($t)) {
                return new WP_Error('artisan_quote', 'Unknown quote.', array('status' => 404));
            }
            $p = $req->get_json_params();
            if (empty($p['rights'])) {
                return new WP_Error('artisan_quote', 'Please confirm you own this model or have permission to have it printed.', array('status' => 400));
            }
            // the price always comes from the hub, never from the browser
            $q = artisan_quote_hub('POST', '/api/store/quotes/' . $t . '/choose', array(
                'timeout' => 20, 'headers' => array('Content-Type' => 'application/json'),
                'body' => wp_json_encode(array('material' => (string) ($p['material'] ?? ''), 'colour' => $p['colour'] ?? null,
                                               'quantity' => (int) ($p['quantity'] ?? 1))),
            ));
            if (is_wp_error($q)) {
                return $q;
            }
            $cfg = artisan_quote_hub('GET', '/api/store/quotes/config', array('timeout' => 10));
            $pid = is_array($cfg) ? (int) ($cfg['woo_product_id'] ?? 0) : 0;
            if (!$pid || !function_exists('wc_load_cart')) {
                return new WP_Error('artisan_quote', 'Custom prints are not open for orders yet.', array('status' => 503));
            }
            wc_load_cart();
            foreach (WC()->cart->get_cart() as $key => $item) {
                if (!empty($item['artisan_quote']) && $item['artisan_quote'] === $t) {
                    WC()->cart->remove_cart_item($key);
                }
            }
            $label = '';
            foreach ($q['options'] as $o) {
                if ($o['key'] === $q['choice']['material']) {
                    $label = $o['label'];
                }
            }
            $added = WC()->cart->add_to_cart($pid, 1, 0, array(), array(
                'artisan_quote' => $t,
                'artisan_quote_total' => (float) $q['choice']['total'],
                'artisan_quote_desc' => array(
                    'Model' => $q['filename'],
                    'Material' => trim($label . ' ' . ($q['choice']['colour'] ?? '')),
                    'Pieces' => (string) $q['choice']['quantity'],
                ),
            ));
            if (!$added) {
                return new WP_Error('artisan_quote', 'Could not add the print to your cart.', array('status' => 500));
            }
            return array('ok' => true, 'cart_url' => wc_get_cart_url(), 'total' => $q['choice']['total']);
        },
    ));
});

/* ----------------------------------------------- cart, order and display */
add_action('woocommerce_before_calculate_totals', function ($cart) {
    foreach ($cart->get_cart() as $item) {
        if (isset($item['artisan_quote_total'])) {
            $item['data']->set_price((float) $item['artisan_quote_total']);
        }
    }
}, 20);

add_filter('woocommerce_get_item_data', function ($data, $item) {
    if (!empty($item['artisan_quote_desc'])) {
        foreach ($item['artisan_quote_desc'] as $k => $v) {
            $data[] = array('key' => $k, 'value' => esc_html($v));
        }
    }
    return $data;
}, 10, 2);

add_filter('woocommerce_add_cart_item_data', function ($data) {
    return $data; // quote data only ever comes from the REST relay above
}, 10, 1);

add_action('woocommerce_checkout_create_order_line_item', function ($line, $key, $values) {
    if (!empty($values['artisan_quote'])) {
        $line->add_meta_data('_artisan_quote', $values['artisan_quote'], true);
        foreach ((array) ($values['artisan_quote_desc'] ?? array()) as $k => $v) {
            $line->add_meta_data($k, $v, true);
        }
    }
}, 10, 3);

add_filter('woocommerce_cart_item_quantity', function ($html, $key, $item) {
    return !empty($item['artisan_quote']) ? '1' : $html;   // pieces are part of the quote
}, 10, 3);

/* ------------------------------------------------- the page and shortcode */
add_action('init', function () {
    if (get_option('artisan_quote_page_id')) {
        return;
    }
    if (!function_exists('wp_insert_post') || !did_action('init')) {
        return;
    }
    $id = wp_insert_post(array(
        'post_title' => 'Custom 3D prints', 'post_name' => 'custom-3d-prints', 'post_status' => 'draft', 'post_type' => 'page',
        'post_content' => "<p>Have your own model printed. Upload an STL, 3MF or OBJ file and get a price straight away.</p>\n[artisan_quote]",
    ));
    if ($id && !is_wp_error($id)) {
        update_option('artisan_quote_page_id', $id);
    }
}, 99);

add_shortcode('artisan_quote', function () {
    $api = esc_url_raw(rest_url('artisan/v1/quote'));
    $nonce = wp_create_nonce('wp_rest');
    $cur = function_exists('get_woocommerce_currency_symbol') ? get_woocommerce_currency_symbol() : '$';
    ob_start(); ?>
<div class="artisan-quote" data-api="<?php echo esc_attr($api); ?>" data-nonce="<?php echo esc_attr($nonce); ?>" data-cur="<?php echo esc_attr(html_entity_decode($cur)); ?>">
  <style>
    .artisan-quote{max-width:720px;border:1px solid rgba(0,0,0,.12);border-radius:12px;padding:20px;margin:1.5em 0}
    .artisan-quote .aq-row{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:10px 0}
    .artisan-quote .aq-opt{border:1px solid rgba(0,0,0,.15);border-radius:10px;padding:10px 12px;cursor:pointer;flex:1 1 200px}
    .artisan-quote .aq-opt.on{border-color:currentColor;box-shadow:0 0 0 2px rgba(0,0,0,.08)}
    .artisan-quote .aq-sw{display:inline-block;width:22px;height:22px;border-radius:50%;border:1px solid rgba(0,0,0,.25);vertical-align:middle;margin-right:6px;cursor:pointer}
    .artisan-quote .aq-sw.on{outline:2px solid currentColor;outline-offset:2px}
    .artisan-quote .aq-sw.out{opacity:.3;cursor:not-allowed}
    .artisan-quote .aq-price{font-size:1.6em;font-weight:700}
    .artisan-quote .aq-muted{opacity:.7;font-size:.9em}
    .artisan-quote .aq-err{color:#b91c1c}
    .artisan-quote img{max-width:100%;border-radius:8px;background:rgba(0,0,0,.04)}
  </style>
  <div class="aq-step1">
    <p><label>Your model (STL, 3MF or OBJ)<br><input type="file" class="aq-file" accept=".stl,.3mf,.obj"></label></p>
    <p class="aq-muted">We slice it on our printers to work out the filament and time. Big or detailed models can take a minute.</p>
  </div>
  <div class="aq-status aq-muted" aria-live="polite"></div>
  <div class="aq-step2" hidden>
    <div class="aq-row"><img class="aq-img" alt="Your model, as it will print" hidden></div>
    <div class="aq-size aq-muted"></div>
    <div class="aq-mats aq-row"></div>
    <div class="aq-cols aq-row"></div>
    <div class="aq-row"><label>Pieces <input type="number" class="aq-qty" min="1" value="1" style="width:5em"></label></div>
    <div class="aq-row"><span class="aq-price"></span><span class="aq-muted aq-note"></span></div>
    <p><label><input type="checkbox" class="aq-rights"> I own this model, or have the designer's permission to have it printed and sold to me.</label></p>
    <p class="aq-muted">We check every custom order before printing. If we can't print it, we'll tell you and refund it.</p>
    <button type="button" class="button aq-cart">Add to cart</button>
  </div>
  <script>
  (function(){
    var root=document.currentScript.closest('.artisan-quote'), api=root.dataset.api, nonce=root.dataset.nonce, cur=root.dataset.cur;
    var $=function(s){return root.querySelector(s)}, st=$('.aq-status'), q=null, mat=null, col=null, timer=null, busy=false;
    function req(m,u,b,isForm){var o={method:m,headers:{'X-WP-Nonce':nonce},credentials:'same-origin'};
      if(b){if(isForm){o.body=b}else{o.body=JSON.stringify(b);o.headers['Content-Type']='application/json'}}
      return fetch(u,o).then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.message||'Something went wrong');return j})})}
    function money(v){return cur+Number(v).toFixed(2)}
    function show(){ if(!q)return; $('.aq-step2').hidden=q.status!=='ready';
      if(q.status==='slicing'){st.textContent='Working out your price...';return}
      if(q.status==='failed'){st.innerHTML='<span class="aq-err">We can\'t quote this model: '+(q.error||'')+'</span>';return}
      if(q.status==='expired'){st.innerHTML='<span class="aq-err">This quote has expired. Upload the model again.</span>';return}
      st.textContent='';
      if(q.previews&&q.previews[0]){var im=$('.aq-img');im.src=api+'/'+q.token+'/'+q.previews[0];im.hidden=false}
      $('.aq-size').textContent=(q.size_mm?q.size_mm.join(' x ')+' mm, ':'')+(q.print_hours?'about '+q.print_hours+' h to print each':'');
      if(!mat&&q.options.length){mat=q.options[0].key}
      var mats=$('.aq-mats');mats.innerHTML='';
      q.options.forEach(function(o){var d=document.createElement('div');d.className='aq-opt'+(o.key===mat?' on':'');
        d.innerHTML='<b></b><div class="aq-muted"></div><div></div>';d.children[0].textContent=o.label;d.children[1].textContent=o.note||'';d.children[2].textContent='from '+money(o.unit_price)+' each';
        d.onclick=function(){mat=o.key;col=null;choose()};mats.appendChild(d)});
      var o=q.options.find(function(x){return x.key===mat}), cols=$('.aq-cols');cols.innerHTML='';
      if(o){ if(!col){var f=o.colours.find(function(c){return c.in_stock});col=f?f.name:null}
        o.colours.forEach(function(c){var s=document.createElement('span');s.className='aq-sw'+(c.name===col?' on':'')+(c.in_stock?'':' out');
          s.style.background=c.hex||'#ccc';s.title=c.name+(c.in_stock?'':' (out of stock)');if(c.in_stock)s.onclick=function(){col=c.name;choose()};cols.appendChild(s)});
        var lab=document.createElement('span');lab.textContent=col||'';cols.appendChild(lab)}
      var ch=q.choice||{}; if(ch.total!=null&&ch.material===mat){$('.aq-price').textContent=money(ch.total);
        $('.aq-note').textContent=' for '+ch.quantity+' piece'+(ch.quantity>1?'s':'')+(q.setup_fee?' (includes '+money(q.setup_fee)+' setup; minimum order '+money(q.minimum)+')':'')}
    }
    function poll(){req('GET',api+'/'+q.token).then(function(j){q=j;if(q.status==='slicing'){timer=setTimeout(poll,3000)}else{choose()}}).catch(function(e){st.innerHTML='<span class="aq-err"></span>';st.firstChild.textContent=e.message})}
    function choose(){ if(!q||q.status!=='ready'||busy)return show(); busy=true;
      var qty=Math.max(1,parseInt($('.aq-qty').value||'1',10));
      req('POST',api+'/'+q.token+'/choose',{material:mat,colour:col,quantity:qty}).then(function(j){q=j}).catch(function(e){st.textContent=e.message}).then(function(){busy=false;show()})}
    $('.aq-file').onchange=function(e){var f=e.target.files[0];if(!f)return;clearTimeout(timer);q=null;mat=null;col=null;$('.aq-step2').hidden=true;
      st.textContent='Uploading...';var fd=new FormData();fd.append('file',f);
      req('POST',api,fd,true).then(function(j){q=j;show();poll()}).catch(function(e){st.innerHTML='<span class="aq-err"></span>';st.firstChild.textContent=e.message})};
    $('.aq-qty').onchange=choose;
    $('.aq-cart').onclick=function(){ if(!$('.aq-rights').checked){alert('Please confirm you can have this model printed.');return}
      var b=this;b.disabled=true;req('POST',api+'/'+q.token+'/cart',{material:mat,colour:col,quantity:parseInt($('.aq-qty').value||'1',10),rights:true})
        .then(function(j){location.href=j.cart_url}).catch(function(e){b.disabled=false;st.innerHTML='<span class="aq-err"></span>';st.firstChild.textContent=e.message})};
  })();
  </script>
</div>
<?php
    return ob_get_clean();
});
