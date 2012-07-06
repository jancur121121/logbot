#!/usr/bin/env perl

use strict;
use warnings;

use lib '/home/logbot/logbot/lib';

# because we log times as UTC, force all our timezone dates to UTC
BEGIN { $ENV{TZ} = 'UTC' }

use CGI::Simple;
use DateTime;
use Date::Manip;
use File::Slurp;
use HTTP::BrowserDetect;
use LogBot;
use LogBot::CGI;
use LogBot::Constants;
use LogBot::Template;
use LogBot::Util;

# XXX this should scan @INC, so the path only has to be set once, by 'use lib'
LogBot::Config->init('/home/logbot/logbot/logbot.conf');

our $config = LogBot::Config->instance;
our $cgi = LogBot::CGI->instance;
our $vars = {
    cgi => $cgi,
    config => $config,
};
$cgi->{vars} = $vars;

my $template = LogBot::Template->new();

parse_parameters();

if ($vars->{action} eq 'json') {
    print $cgi->header(-type => 'application/json', -charset => 'utf-8');

    require Mojo::JSON;
    my $json = Mojo::JSON->new;

    $SIG{__DIE__} = sub {
        my $error = shift;
        print $json->encode({ error => $error });
        exit;
    };

    my $channel = $vars->{channel};
    my $request = $vars->{r};
    if ($request eq 'channel_data') {
        my $last_message = $channel->last_message;
        my $last_updated;
        if ($last_message) {
            $last_updated = $last_message->datetime->strftime('%d %b %Y %H:%M:%S')
        } else {
            $last_updated = '';
        }

        print $json->encode({
            database_size => pretty_size($channel->database_size),
            last_updated => $last_updated,
            event_count => commify($channel->event_count),
        });

    } elsif ($request eq 'channel_last_updated') {
        my $last_message = $channel->last_message;
        my $last_updated;
        if ($last_message) {
            $last_updated = $last_message->datetime->strftime('%d %b %Y %H:%M:%S')
        } else {
            $last_updated = '';
        }
        print $json->encode({
            last_updated => $last_updated,
        });

    } elsif ($request eq 'channel_database_size') {
        print $json->encode({
            database_size => pretty_size($channel->database_size),
        });

    } elsif ($request eq 'channel_event_count') {
        print $json->encode({
            event_count => commify($channel->event_count),
        });

    } elsif ($request eq 'channel_plot_hours') {
        my $network = $channel->{network};
        my $channel_name = $channel->{name};
        $channel_name =~ s/^#//;
        print read_file(
            $config->{data_path} . '/plot/hours/' . $channel->{network} . "-$channel_name.json"
        );

    }

    exit;
}

# force queries from robots to a single date
my $is_robot = HTTP::BrowserDetect::robot();
if ($is_robot) {
    if ($vars->{action} eq 'browse') {
        $vars->{end_date} = $vars->{start_date}->clone->add(days => 1);
        $vars->{h} = '';
        $vars->{j} = 0;
        $vars->{b} = 0;
    } elsif ($vars->{action} ne 'about') {
        $vars->{action} = 'browse';
        delete $vars->{run};
    }
}

print $cgi->header(-charset => 'utf-8');

if ($vars->{action} eq 'browse') {
    $cgi->{fields} = [qw(c s e j b h)];
} elsif ($vars->{action} eq 'search') {
    $cgi->{fields} = [qw(a c q ss se)];
}

$template->render('header.html', vars => $vars);

if ($vars->{action} eq 'about') {

    $template->render('about.html', vars => $vars);

} else {

    if ($is_robot) {
        # give search crawlers a direct link to each log by date
        if (!$vars->{run}) {
            my $channel = $vars->{channel};
            my $first_event = $channel->first_event;
            my $last_event  = $channel->last_event;
            my $first_date = $first_event->datetime;
            my $last_date = $last_event->datetime
                                    ->clone()
                                    ->truncate(to => 'day')
                                    ->add(days => 1)
                                    ->add(nanoseconds => -1);
            my $date = $first_date->clone()
                              ->truncate(to => 'day');
            my @dates;
            while ($date < $last_date) {
                push @dates, $date->format_cldr('d MMM y');
                $date->add(days => 1);
            }
            $vars->{dates} = \@dates;
        }
        $template->render('robots.html', vars => $vars);
    } else {
        $template->render('tabs.html', vars => $vars);
    }

    if ($vars->{run} && !$vars->{error}) {
        my %args = (
            channel => $vars->{channel},
        );

        if ($vars->{action} eq 'browse') {
            # browse
            $args{template}      = 'browse';
            $args{start_date}    = $vars->{start_date};
            $args{end_date}      = $vars->{end_date};
            $args{hilite}        = $vars->{h};
            $args{messages_only} = !$vars->{j};
            $args{empty_dates}   = 1;
            if ($vars->{b}) {
                $args{exclude_nicks} = $vars->{network}->{bots};
            }

        } else {
            # search
            $args{template}      = 'search';
            $args{start_date}    = $vars->{search_start_date};
            $args{end_date}      = $vars->{search_end_date};
            $args{hilite}        = $vars->{q};
            $args{messages_only} = 1;
            $args{empty_dates}   = 0;
            $args{limit}         = $config->{web}->{search_limit};
            if ($vars->{q} =~ s/<([^>]+)>//) {
                $args{nick} = $1;
            }
            $args{include_text} = $vars->{q};
        }

        show_events(%args);
    }
}

$template->render('footer.html');

#

sub parse_parameters {

    # short-circuit default page

    if (!$ENV{QUERY_STRING} || $ENV{QUERY_STRING} eq '') {
        $vars->{action} = 'about';
        return;
    }

    # split network and channel

    my ($network_name, $channel_name);
    $channel_name = $cgi->param('c');
    if (!defined $channel_name || $channel_name eq '') {
        $network_name = $config->{web}->{default_network};
        $channel_name = $config->{web}->{default_channel};
    } elsif ($channel_name =~ /^([^#]+)(#.+)$/) {
        ($network_name, $channel_name) = ($1, $2);
    } else {
        $network_name = $config->{web}->{default_network};
        $channel_name = '#' . $channel_name unless $channel_name =~ /^#/;
    }

    # validate network

    my $network = $config->network($network_name);
    if (!$network) {
        $network = $config->network($config->{web}->{default_network});
    }
    $vars->{network} = $network;

    # validate channel

    my $channel = $network->channel($channel_name);
    if (!$channel || !$channel->{public}) {
        $vars->{error} = "Unsupported channel $channel_name";
        $vars->{action} = 'about';
        return;
    }
    $vars->{channel} = $channel;

    $vars->{c} = "$network_name$channel_name";

    # action

    my $action = $cgi->param('a');
    $action = '' if !defined($action) || ($action ne 'search' && $action ne 'json');

    if ($action eq '') {
        delete $vars->{a};
        $vars->{action} = 'browse';

        # browse

        $vars->{run} = 0;
        $vars->{s}   = $cgi->param('s');
        $vars->{e}   = $cgi->param('e');
        $vars->{j}   = $cgi->param('j');
        $vars->{b}   = $cgi->param('b');

        # start date

        if ($cgi->param('s')) {
            my $start_time = UnixDate(lc($cgi->param('s')) . ' 00:00:00', '%s');
            unless (defined $start_time) {
                $vars->{error} = 'Invalid start date';
                return;
            }
            $vars->{run} = 1;
            $vars->{start_date} = DateTime->from_epoch(epoch => $start_time);
        }

        # end date

        if ($cgi->param('e')) {
            my $end_time = UnixDate(lc($cgi->param('e')) . ' 23:59:59', '%s');
            unless (defined $end_time) {
                $vars->{error} = 'Invalid end date';
                return;
            }
            $vars->{run} = 1;
            $vars->{end_date} = DateTime->from_epoch(epoch => $end_time);
        }

        # hilite (not in UI, for backwards compatibility)

        if (defined $cgi->param('h') && $cgi->param('h') ne '') {
            $vars->{h} = $cgi->param('h');
        } else {
            delete $vars->{h};
        }

        # boolen args

        if (defined $cgi->param('j')) {
            $vars->{j} = $cgi->param('j') eq '1' ? '1' : '0';
        }
        delete $vars->{j} unless $vars->{j};

        if (defined $cgi->param('b')) {
            $vars->{b} = $cgi->param('b') eq '1' ? '1' : '0';
        }
        delete $vars->{b} unless $vars->{b};

    } elsif ($action eq 'json') {
        $vars->{action} = 'json';

        # json data

        $vars->{r} = $cgi->param('r');

        # no need to prefill data for tabs

        return;

    } else {

        $vars->{a} = 'search';
        $vars->{action} = 'search';

        # search

        $vars->{q}  = $cgi->param('q');
        $vars->{ss} = $cgi->param('ss');
        $vars->{se} = $cgi->param('se');

        # query

        if (defined $cgi->param('q')) {
            my $query = $cgi->param('q');
            $query =~ s/(^\s+|\s+$)//g;
            if ($query ne '') {
                $vars->{run} = 1;
                $vars->{q} = $query;
            }
        }

        # start date

        if ($cgi->param('ss')) {
            my $start_time = UnixDate(lc($cgi->param('ss')) . ' 00:00:00', '%s');
            unless (defined $start_time) {
                $vars->{error} = 'Invalid start date';
                return;
            }
            $vars->{search_start_date} = DateTime->from_epoch(epoch => $start_time);
            $vars->{ss} = $vars->{search_start_date}->format_cldr('d MMM y');
        } else {
            delete $vars->{search_start_date};
            delete $vars->{ss};
        }

        # end date

        if ($cgi->param('se')) {
            my $end_time = UnixDate(lc($cgi->param('se')) . ' 23:59:59', '%s');
            unless (defined $end_time) {
                $vars->{error} = 'Invalid end date';
                return;
            }
            $vars->{search_end_date} = DateTime->from_epoch(epoch => $end_time);
            $vars->{se} = $vars->{search_end_date}->format_cldr('d MMM y');
        }

        # ensure start date < end date

        if ($vars->{search_start_date} &&
            $vars->{search_end_date} &&
            $vars->{search_start_date} > $vars->{search_end_date}
        ) {
            $vars->{search_end_date} = $vars->{search_start_date}
                                       ->clone
                                       ->truncate(to => 'day')
                                       ->add(days => 1)
                                       ->add(nanoseconds => -1);
        }

    }

    # we always want dates on the browse tab

    $vars->{start_date} ||= now()
                            ->truncate(to => 'day');
    $vars->{end_date}   ||= now()
                            ->truncate(to => 'day')
                            ->add(days => 1)
                            ->add(nanoseconds => -1);

    # ensure start date < end date

    if ($vars->{start_date} > $vars->{end_date}) {
        $vars->{end_date} = $vars->{start_date}
                            ->clone
                            ->truncate(to => 'day')
                            ->add(days => 1)
                            ->add(nanoseconds => -1);
    }

    # don't allow massive date spans when browsing

    if ($vars->{action} eq 'browse' && $vars->{run}) {
        if ($vars->{start_date}->delta_days($vars->{end_date})->in_units('days') > MAX_BROWSE_DAY_SPAN) {
            $vars->{error} = 'You cannot browse dates greater than ' . MAX_BROWSE_DAY_SPAN . ' days appart.';
            return;
        }
    }

    # format dates for display

    $vars->{s} = $vars->{start_date}->format_cldr('d MMM y');
    $vars->{e} = $vars->{end_date}->format_cldr('d MMM y');

    # we always want a default search start date

    if ($vars->{action} ne 'search') {
        $vars->{search_start_date} = now()
                                     ->truncate(to => 'day')
                                     ->add(months => -1);
        $vars->{ss} = $vars->{search_start_date}->format_cldr('d MMM y');
    }
}

#

sub show_events {
    my (%args) = @_;

    my $template_dir = $args{template};
    $template->render("$template_dir/header.html", vars => $vars);

    # build filters

    my %filter = (
        order => 'time',
    );
    if ($args{start_date}) {
        $filter{date_after} = $args{start_date}->epoch;
    }
    if ($args{end_date}) {
        $filter{date_before} = $args{end_date}->epoch;
    }
    if ($args{include_text}) {
        $filter{include_text} = [ $args{include_text} ];
    }
    if ($args{nick}) {
        $filter{nick} = $args{nick};
    }
    if ($args{messages_only}) {
        $filter{events} = [ EVENT_PUBLIC, EVENT_ACTION ];
    }
    if ($args{limit}) {
        $filter{limit_last} = $args{limit};
    }
    if ($args{exclude_nicks}) {
        $filter{exclude_nicks} = $args{exclude_nicks};
    }
    if ($cgi->param('debug')) {
        $filter{debug_sql} = 1;
    }

    # init hiliting

    my $hilite;
    if (exists $args{hilite} && defined($args{hilite})) {
        $hilite = $args{hilite};
        $hilite =~ s/</\000/g;
        $hilite =~ s/>/\001/g;
        $hilite =~ s/&/\002/g;
    }

    # init date tracking and counting

    my $current_date;
    if ($args{start_date}
        && $args{end_date}
        && $args{start_date}->ymd('') eq $args{end_date}->ymd('')
    ) {
        $current_date = 0;
    } elsif (!$args{start_date}) {
        $current_date = 0;
    } else {
        $current_date = $args{start_date}
                        ->clone
                        ->truncate(to => 'day')
                        ->add(days => -1);
    }
    my $today_date = now()->truncate(to => 'day');

    my $last_event = 0;
    my $event_count = 0;

    # hit the db

    $args{channel}->browse(
        %filter,
        callback    => sub {
            my $event = shift;
            $last_event = $event;
            $event_count++;

            # new date header

            if (!$current_date || $event->date ne $current_date) {

                if ($args{empty_dates}) {

                    # show day even if there's no messages

                    if ($current_date) {
                        $current_date->add(days => 1);
                        while ($current_date < $event->date) {
                            $template->render(
                                "$template_dir/date.html",
                                date => $current_date,
                                prev => $current_date->clone->add(days => -1),
                                next => $current_date->clone->add(days => 1),
                            );
                            $template->render("$template_dir/empty.html");
                            $current_date->add(days => 1);
                        }
                    }

                }

                # date header

                $template->render(
                    "$template_dir/date.html",
                    date => $event->date,
                    prev => $event->date->clone->add(days => -1),
                    next => $event->date->clone->add(days => 1),
                );
                $current_date = $event->date;
            }

            # linkify text
            if (defined $hilite) {
                $event->{text} = hilite($event->{text}, $hilite);
            } else {
                Mojo::Util::xml_escape($event->{text});
                $event->{text} = linkify($event->{text});
            }

            $template->render("$template_dir/content.html", vars => $vars, event => $event);

            return 1;
        },
    );

    if ($args{empty_dates}) {

        # show empty days after the last found event
        # this also duplicates the header for the last date if there were any events

        if ($current_date) {
            $current_date->add(days => 1);
        } else {
            $current_date = $args{start_date}->clone->truncate(to => 'day');
        }
        my $trailing_dates = 0;
        while ($current_date <= $args{end_date}) {
            last if $current_date > $today_date;
            $trailing_dates = 1;
            $template->render(
                "$template_dir/date.html",
                date => $current_date,
                prev => $current_date->clone->add(days => -1),
                next => $current_date->clone->add(days => 1),
            );
            $template->render("$template_dir/empty.html");
            $current_date->add(days => 1);
        }

        # if we output nothing (such as all dates are in the future), show something
        if (!$trailing_dates && !$last_event) {
            $current_date = $args{start_date}->clone->truncate(to => 'day');
            $template->render(
                "$template_dir/date.html",
                date => $current_date,
                prev => $current_date->clone->add(days => -1),
                next => $current_date->clone->add(days => 1),
            );
            $template->render("$template_dir/empty.html");
        }
    }

    # show footer date

    if ($current_date) {
        $current_date->add(days => -1);
        if ($last_event
            && $current_date->ymd() eq $last_event->datetime->ymd()
        ) {
            $template->render(
                "$template_dir/date.html",
                date => $current_date,
                prev => $current_date->clone->add(days => -1),
                next => $current_date->clone->add(days => 1),
            );
        }
    }

    $vars->{last_event}  = $last_event;
    $vars->{event_count} = $event_count;
    $template->render("$template_dir/footer.html", vars => $vars);
}

#

sub linkify {
    # XXX move to util
    my ($value, $rs_href) = @_;
    $rs_href ||= sub { $_[0] };

    # munge email addresses
    $value =~ s#([a-zA-Z0-9\.-]+)\@(([a-zA-Z0-9\.-]+\.)+[a-zA-Z0-9\.-]+)#$1\%$2#g;

    unless ($value =~ s#&lt;(https?://.+?)&gt;#'&lt;<a href="' . $rs_href->($1) . '">' . shorten($1) . '</a>&gt;'#ge) {
        $value =~ s#(https?://[^\s\b]+)#'<a href="' . $rs_href->($1) . '">' . shorten($1) . '</a>'#ge;
    }

    # bugzilla urls
    $value =~ s#(\bbug\s+(\d+))#<a href="https://bugzilla.mozilla.org/show_bug.cgi?id=$2">$1</a>#gi;
    $value =~ s#(\battachment\s+(\d+))#<a href="https://bugzilla.mozilla.org/attachment.cgi?id=$2&action=edit">$1</a>#gi;

    return $value;
}

sub hilite {
    # XXX move to util
    my ($value, $hilite) = @_;

    $value =~ s/</\000/g;
    $value =~ s/>/\001/g;
    $value =~ s/&/\002/g;

    Mojo::Util::xml_escape($value);
    $value =~ s#($hilite)#\003$1\004#goi;

    $value =~ s/\000/&lt;/g;
    $value =~ s/\001/&gt;/g;
    $value =~ s/\002/&amp;/g;

    $value = linkify(
        $value,
        sub {
            my $value = shift;
            $value =~ s/[\003\004]//g;
            return $value;
        }
    );

    $value =~ s#\003#<span class="hilite">#g;
    $value =~ s#\004#</span>#g;

    return $value;
}


sub shorten {
    # XXX move to util
    my ($value) = @_;
    return $value if length($value) < 70;
    while (length($value) >= 70) {
        substr($value, length($value) / 2 - 1, 3) = '';
    }
    substr($value, length($value) / 2, 3) = '...';
    return $value;
}

