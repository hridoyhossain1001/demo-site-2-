<?php
/**
 * Fired when the plugin is uninstalled.
 *
 * @package Buykori_AdSync
 */

// If uninstall not called from WordPress, then exit.
if (!defined('WP_UNINSTALL_PLUGIN')) {
	exit;
}

// 1. Delete Database Options
delete_option('buykorigw_settings');
delete_option('buykorigw_custom_events');

// 2. Delete Transients
delete_transient('buykorigw_update_info');

// 3. Clear Scheduled Actions (WP-Cron)
$timestamp = wp_next_scheduled('buykorigw_retry_confirm_cron');
if ($timestamp) {
	wp_unschedule_event($timestamp, 'buykorigw_retry_confirm_cron');
}
wp_clear_scheduled_hook('buykorigw_sync_purchase');
wp_clear_scheduled_hook('buykorigw_retry_event_cron');

// Action Scheduler actions will be cleaned up by Action Scheduler itself over time,
// but we can explicitly cancel pending actions if Action Scheduler is active.
if (function_exists('as_unschedule_all_actions')) {
	as_unschedule_all_actions('buykorigw_retry_confirm');
	as_unschedule_all_actions('buykorigw_retry_cancel');
	as_unschedule_all_actions('buykorigw_sync_purchase');
	as_unschedule_all_actions('buykorigw_retry_event');
}

// Note: We deliberately do NOT delete order meta data (_buykorigw_tracked, etc.)
// because order history and tracking status should be preserved even if the 
// plugin is temporarily uninstalled or replaced.
